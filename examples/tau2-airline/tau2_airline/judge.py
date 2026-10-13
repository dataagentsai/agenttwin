"""One trajectory, two judges — the airline's bench over the shared judges.

`tau2_common.judge` says what each judge does. Here: τ²'s airline database
and the AgentTwin world made from it, and who the customer is (the user id
the task's own words give).

A trajectory is a list of steps: `("call", name, arguments)` or
`("say", customer_words, agent_words)`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tau2.data_model.tasks import Task
from tau2.domains.airline.data_model import FlightDB
from tau2.domains.airline.environment import get_environment
from tau2.domains.airline.utils import AIRLINE_DB_PATH
from tau2_airline.standin import view
from tau2_common import judge as common
from tau2_common.judge import AgentTwinVerdict, Step, Tau2Verdict, differences

HERE = Path(__file__).resolve().parents[1]
WORLD = HERE / "world.yaml"

_UID = re.compile(r"user id is[:\s'\"]*([a-z]+_[a-z]+_[0-9]+)", re.IGNORECASE)


def session_user(task: Task) -> str:
    """The user id the customer will give: the task's own words for who they are."""
    instructions = task.user_scenario.instructions
    for text in (instructions.known_info, instructions.task_instructions, instructions.reason_for_call):
        found = _UID.search(text or "")
        if found:
            return found.group(1)
    raise ValueError(f"task {task.id}: no user id in the customer's instructions")


class Bench(common.Bench):
    """Loaded once: τ²'s database, and the AgentTwin world made from it."""

    name = "tau2-airline"
    view = staticmethod(view)
    constructor = staticmethod(get_environment)

    def __init__(self) -> None:
        self.db = FlightDB.load(AIRLINE_DB_PATH)
        super().__init__(WORLD)

    def fresh(self) -> Any:
        return get_environment(db=self.db.model_copy(deep=True))

    def session(self, task: Task) -> dict[str, str]:
        return {"user_id": session_user(task)}


async def agenttwin(bench: Bench, user_id: str, steps: list[Step]) -> AgentTwinVerdict:
    return await common.agenttwin(bench, None, {"user_id": user_id}, steps)


def messages(bench: Bench, steps: list[Step]) -> tuple[list[Any], list[tuple[bool, str]]]:
    return common.messages(bench, None, steps)


def tau2(bench: Bench, task: Task, steps: list[Step]) -> Tau2Verdict:
    return common.tau2(bench, task, steps)


__all__ = ["HERE", "WORLD", "AgentTwinVerdict", "Bench", "Step", "Tau2Verdict", "agenttwin",
           "differences", "messages", "session_user", "tau2"]
