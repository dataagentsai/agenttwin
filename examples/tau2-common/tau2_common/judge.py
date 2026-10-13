"""One trajectory, two judges — for any τ² domain.

**AgentTwin** drives the trajectory through the stand-in over MCP, as the
customer the task names (the session), and its monitor watches every call and
reply: `own-rows-only`, `at-most-once`, `reply-true`, `owed-overdue` (the
defaults) and `within-spec`. The verdict is *no incident*. It knows the AOAS
and the world, and nothing of the task's expected actions.

**τ²** judges the same trajectory with its own evaluators, unchanged, over the
task's `reward_basis`, as `evaluate_simulation` combines them: `DB` and
`ENV_ASSERTION` (EnvironmentEvaluator: the database after the trajectory must
hash equal to the database after the expected actions; τ²'s own assertions
must hold), `ACTION` (every expected action was called), `COMMUNICATE` (every
`communicate_info` string was said). `NL_ASSERTION` is judged by a model in τ²
and is **not run here** (offline, no model calls); a task that has some is
marked, and its τ² verdict is the rest of its basis.

**Fidelity**: τ²'s messages are made by τ²'s own environment answering each
call. The stand-in's answers to the same calls are compared with them, byte
for byte, and every difference is reported.

A trajectory is a list of steps:

| step | what |
|---|---|
| `("call", name, arguments)` | the agent calls one of its tools |
| `("user_call", name, arguments)` | the customer uses one of their own tools (telecom's phone) |
| `("say", customer_words, agent_words)` | a turn of talk |
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from mcp.client import Client

from agenttwin import SESSION_META, Live, Moment, Monitor, WithinSpec, default_watches, load, with_records
from tau2.data_model.message import AssistantMessage, ToolCall, ToolMessage, UserMessage
from tau2.data_model.tasks import RewardType, Task
from tau2.environment.toolkit import get_tool_types
from tau2.evaluator.evaluator_action import ActionEvaluator
from tau2.evaluator.evaluator_communicate import CommunicateEvaluator
from tau2.evaluator.evaluator_env import EnvironmentEvaluator
from tau2_common.standin import Standin, View

logger.remove()
logger.add(sys.stderr, level="ERROR")  # τ²'s tools log every record they touch

Step = tuple[Any, ...]


class Bench:
    """One domain, loaded once: τ²'s database, and the AgentTwin world made from it.

    A domain supplies `name`, `view` (τ²'s environment → the AOAS's rows),
    `fresh` (a new τ² environment over a copy of the database), `constructor`
    (τ²'s `get_environment`, for its evaluator) and `session` (who the
    customer says they are, from the task's own words)."""

    name = "tau2"
    view: View
    constructor: Any

    def __init__(self, world: Path) -> None:
        base = self.fresh()
        self.world = with_records(load(world), self.view(base))
        self.rows = {
            e: {str(r[self.world.entities[e].key]): r for r in rs} for e, rs in self.world.records.items()
        }

    def fresh(self) -> Any:  # pragma: no cover - each domain's
        raise NotImplementedError

    def session(self, task: Task) -> dict[str, str]:  # pragma: no cover - each domain's
        raise NotImplementedError

    def env(self, task: Task | None = None) -> Any:
        """A τ² environment in the task's initial state, as τ²'s evaluator sets it."""
        env = self.fresh()
        state = task.initial_state if task is not None else None
        if state is not None:
            env.set_state(
                initialization_data=state.initialization_data,
                initialization_actions=state.initialization_actions,
                message_history=list(state.message_history or []),
            )
        return env


@dataclass
class AgentTwinVerdict:
    incidents: list[dict[str, Any]]
    answers: list[tuple[bool, str]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.incidents


def _who(session: dict[str, str]) -> str:
    return str(next(iter(session.values())))


async def agenttwin(bench: Bench, task: Task | None, session: dict[str, str], steps: list[Step]) -> AgentTwinVerdict:
    live = Live.start(bench.world)
    standin = Standin(live, bench.env(task), bench.view, bench.name)
    if task is not None and task.initial_state is not None:
        standin.sync()  # the task's own starting state, before anyone speaks
    who = _who(session)
    key = next(iter(session))
    monitor = Monitor(
        live,
        watches=[*default_watches(owed_within_s=3600), WithinSpec()],
        session_of=lambda cid: {key: cid},
    )
    answers: list[tuple[bool, str]] = []
    turn = 0
    async with Client(standin.server(wrap=monitor.wrap)) as client:
        monitor.begin(Moment(at=0, customer_id=who, turn_id="t0", session=session), "")
        for step in steps:
            if step[0] == "call":
                result = await client.call_tool(step[1], step[2], meta={SESSION_META: session})
                answers.append((bool(result.is_error), result.content[0].text if result.content else ""))
            elif step[0] == "user_call":
                answers.append(standin.customer_acts(step[1], step[2]))
            else:
                turn += 1
                monitor.begin(Moment(at=turn, customer_id=who, turn_id=f"t{turn}", session=session), step[1])
                monitor.replied(step[2])
    return AgentTwinVerdict(
        incidents=[{"rule": i.rule, "root": i.root, "detail": i.detail} for i in monitor.incidents],
        answers=answers,
    )


@dataclass
class Tau2Verdict:
    reward: float
    parts: dict[str, float]
    """Each reward the task's basis names, as τ² computed it."""
    answers: list[tuple[bool, str]]
    unjudged: list[str] = field(default_factory=list)
    """Parts of the basis not run here: `NL_ASSERTION`, which needs a model."""

    @property
    def passed(self) -> bool:
        return self.reward == 1.0

    @property
    def db(self) -> float | None:
        return self.parts.get(RewardType.DB.value)

    @property
    def communicate(self) -> float | None:
        return self.parts.get(RewardType.COMMUNICATE.value)


def messages(bench: Bench, task: Task | None, steps: list[Step]) -> tuple[list[Any], list[tuple[bool, str]]]:
    """τ²'s messages for a trajectory, each tool answer made by τ²'s own environment."""
    env = bench.env(task)
    out: list[Any] = []
    answers: list[tuple[bool, str]] = []
    for i, step in enumerate(steps):
        if step[0] in ("call", "user_call"):
            requestor = "user" if step[0] == "user_call" else "assistant"
            call = ToolCall(id=f"call_{i}", name=step[1], arguments=step[2], requestor=requestor)
            if requestor == "user":
                out.append(UserMessage(role="user", content=None, tool_calls=[call]))
            else:
                out.append(AssistantMessage(role="assistant", content=None, tool_calls=[call]))
            answer: ToolMessage = env.get_response(call)
            out.append(answer)
            answers.append((answer.error, answer.content or ""))
        else:
            if step[1]:
                out.append(UserMessage(role="user", content=step[1]))
            out.append(AssistantMessage(role="assistant", content=step[2]))
    return out, answers


def tau2(bench: Bench, task: Task, steps: list[Step]) -> Tau2Verdict:
    trajectory, answers = messages(bench, task, steps)
    criteria = task.evaluation_criteria
    basis = set(criteria.reward_basis) if criteria else set()
    parts: dict[str, float] = {}
    unjudged: list[str] = []
    if basis & {RewardType.DB, RewardType.ENV_ASSERTION}:
        env = EnvironmentEvaluator.calculate_reward(
            environment_constructor=bench.constructor, task=task, full_trajectory=trajectory
        )
        for part in (RewardType.DB, RewardType.ENV_ASSERTION):
            if part in basis:
                parts[part.value] = (env.reward_breakdown or {}).get(part, env.reward)
    if RewardType.ACTION in basis:
        tools = bench.fresh().tools
        parts[RewardType.ACTION.value] = ActionEvaluator.calculate_reward(
            task=task, full_trajectory=trajectory, tool_types=get_tool_types(tools)
        ).reward
    if RewardType.COMMUNICATE in basis:
        parts[RewardType.COMMUNICATE.value] = CommunicateEvaluator.calculate_reward(
            task=task, full_trajectory=trajectory
        ).reward
    if RewardType.NL_ASSERTION in basis:
        if criteria.nl_assertions:
            unjudged.append(RewardType.NL_ASSERTION.value)
        else:
            parts[RewardType.NL_ASSERTION.value] = 1.0  # τ²'s own answer when there are none
    reward = 1.0
    for value in parts.values():
        reward *= value
    return Tau2Verdict(reward=reward, parts=parts, answers=answers, unjudged=unjudged)


def differences(ours: list[tuple[bool, str]], theirs: list[tuple[bool, str]]) -> list[int]:
    """Which calls the stand-in answered differently from τ²'s environment."""
    if len(ours) != len(theirs):
        return list(range(max(len(ours), len(theirs))))
    return [i for i, (a, b) in enumerate(zip(ours, theirs, strict=True)) if a != b]


__all__ = ["AgentTwinVerdict", "Bench", "Step", "Tau2Verdict", "agenttwin", "differences", "messages", "tau2"]
