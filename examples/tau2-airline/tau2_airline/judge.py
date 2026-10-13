"""One trajectory, two judges.

**AgentTwin** drives the trajectory through the stand-in over MCP, as the
customer whose user id the task gives (the session), and its monitor watches
every call and reply: `own-rows-only`, `at-most-once`, `reply-true`,
`owed-overdue` (the defaults) and `within-spec`. The verdict is *no incident*.
It knows the AOAS and the world, and nothing of the task's expected actions.

**τ²** judges the same trajectory with its own evaluator, unchanged: the
database after replaying the trajectory must hash equal to the database after
replaying the task's expected actions (`DB`), and every `communicate_info`
string must appear in what the agent said (`COMMUNICATE`). The reward is the
product over the task's `reward_basis`, as `evaluate_simulation` takes it.

**Fidelity**: τ²'s messages are made by τ²'s own environment answering each
call. The stand-in's answers to the same calls are compared with them, byte
for byte, and every difference is reported.

A trajectory is a list of steps: `("call", name, arguments)` or
`("say", customer_words, agent_words)`.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from mcp.client import Client

from agenttwin import SESSION_META, Live, Moment, Monitor, WithinSpec, default_watches, load, with_records
from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.data_model.tasks import RewardType, Task
from tau2.domains.airline.data_model import FlightDB
from tau2.domains.airline.environment import get_environment
from tau2.domains.airline.utils import AIRLINE_DB_PATH
from tau2.evaluator.evaluator_communicate import CommunicateEvaluator
from tau2.evaluator.evaluator_env import EnvironmentEvaluator
from tau2_airline.convert import rows
from tau2_airline.standin import Standin

logger.remove()
logger.add(sys.stderr, level="ERROR")  # τ²'s tools log every reservation they touch

HERE = Path(__file__).resolve().parents[1]
WORLD = HERE / "world.yaml"
Step = tuple[Any, ...]

_UID = re.compile(r"user id is[:\s'\"]*([a-z]+_[a-z]+_[0-9]+)", re.IGNORECASE)


def session_user(task: Task) -> str:
    """The user id the customer will give: the task's own words for who they are."""
    instructions = task.user_scenario.instructions
    for text in (instructions.known_info, instructions.task_instructions, instructions.reason_for_call):
        found = _UID.search(text or "")
        if found:
            return found.group(1)
    raise ValueError(f"task {task.id}: no user id in the customer's instructions")


class Bench:
    """Loaded once: τ²'s database, and the AgentTwin world made from it."""

    def __init__(self) -> None:
        self.db = FlightDB.load(AIRLINE_DB_PATH)
        env = get_environment(db=self.db.model_copy(deep=True))
        self.world = with_records(load(WORLD), rows(env.tools.db, env.tools.list_all_airports()))
        self.rows = {e: {str(r[self.world.entities[e].key]): r for r in rs} for e, rs in self.world.records.items()}

    def env(self):
        return get_environment(db=self.db.model_copy(deep=True))


@dataclass
class AgentTwinVerdict:
    incidents: list[dict[str, Any]]
    answers: list[tuple[bool, str]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.incidents


async def agenttwin(bench: Bench, user_id: str, steps: list[Step]) -> AgentTwinVerdict:
    live = Live.start(bench.world)
    standin = Standin(live, bench.env())
    session = {"user_id": user_id}
    monitor = Monitor(
        live,
        watches=[*default_watches(owed_within_s=3600), WithinSpec()],
        session_of=lambda uid: {"user_id": uid},
    )
    answers: list[tuple[bool, str]] = []
    turn = 0
    async with Client(standin.server(wrap=monitor.wrap)) as client:
        monitor.begin(Moment(at=0, customer_id=user_id, turn_id="t0", session=session), "")
        for step in steps:
            if step[0] == "call":
                result = await client.call_tool(step[1], step[2], meta={SESSION_META: session})
                answers.append((bool(result.is_error), result.content[0].text if result.content else ""))
            else:
                turn += 1
                monitor.begin(Moment(at=turn, customer_id=user_id, turn_id=f"t{turn}", session=session), step[1])
                monitor.replied(step[2])
    return AgentTwinVerdict(
        incidents=[{"rule": i.rule, "root": i.root, "detail": i.detail} for i in monitor.incidents],
        answers=answers,
    )


@dataclass
class Tau2Verdict:
    reward: float
    db: float | None
    communicate: float | None
    answers: list[tuple[bool, str]]

    @property
    def passed(self) -> bool:
        return self.reward == 1.0


def messages(bench: Bench, steps: list[Step]) -> tuple[list[Any], list[tuple[bool, str]]]:
    """τ²'s messages for a trajectory, each tool answer made by τ²'s own environment."""
    env = bench.env()
    out: list[Any] = []
    answers: list[tuple[bool, str]] = []
    for i, step in enumerate(steps):
        if step[0] == "call":
            call = ToolCall(id=f"call_{i}", name=step[1], arguments=step[2])
            out.append(AssistantMessage(role="assistant", content=None, tool_calls=[call]))
            answer = env.get_response(call)
            out.append(answer)
            answers.append((answer.error, answer.content or ""))
        else:
            if step[1]:
                out.append(UserMessage(role="user", content=step[1]))
            out.append(AssistantMessage(role="assistant", content=step[2]))
    return out, answers


def tau2(bench: Bench, task: Task, steps: list[Step]) -> Tau2Verdict:
    trajectory, answers = messages(bench, steps)
    env = EnvironmentEvaluator.calculate_reward(
        environment_constructor=get_environment, task=task, full_trajectory=trajectory
    )
    communicate = CommunicateEvaluator.calculate_reward(task=task, full_trajectory=trajectory)
    basis = task.evaluation_criteria.reward_basis if task.evaluation_criteria else []
    reward = 1.0
    if RewardType.DB in basis:
        reward *= env.reward
    if RewardType.COMMUNICATE in basis:
        reward *= communicate.reward
    return Tau2Verdict(
        reward=reward,
        db=env.db_check.db_reward if env.db_check else None,
        communicate=communicate.reward,
        answers=answers,
    )


def differences(ours: list[tuple[bool, str]], theirs: list[tuple[bool, str]]) -> list[int]:
    """Which calls the stand-in answered differently from τ²'s environment."""
    if len(ours) != len(theirs):
        return list(range(max(len(ours), len(theirs))))
    return [i for i, (a, b) in enumerate(zip(ours, theirs, strict=True)) if a != b]
