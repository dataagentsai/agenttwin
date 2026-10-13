"""One trajectory, two judges — the retail bench over the shared judges.

`tau2_common.judge` says what each judge does. Here: τ²'s retail database and
the AgentTwin world made from it, and who the customer is.

**Who the customer is.** The policy authenticates a customer by looking their
user id up from their email, or their name and zip code. The task's own words
for the customer (`known_info`) carry one of these, or a user id; the session
is the user that description finds in τ²'s database, the way the lookup tools
find it. Nothing here reads the expected actions.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tau2.data_model.tasks import Task
from tau2.domains.retail.data_model import RetailDB
from tau2.domains.retail.environment import get_environment
from tau2.domains.retail.utils import RETAIL_DB_PATH
from tau2_common import judge as common
from tau2_retail.convert import view

HERE = Path(__file__).resolve().parents[1]
WORLD = HERE / "world.yaml"

_UID = re.compile(r"\b([a-z]+_[a-z]+_[0-9]{4})\b")


def session_user(db: RetailDB, known_info: str) -> str:
    """The one user the customer's self-description names: a user id; else an
    email the store knows; else a full name with that user's zip code."""
    found = _UID.search(known_info)
    if found and found.group(1) in db.users:
        return found.group(1)
    low = known_info.lower()
    for match in (
        lambda u: u.email.lower() in low,
        lambda u: f"{u.name.first_name} {u.name.last_name}" in known_info and u.address.zip in known_info,
    ):
        users = [uid for uid, u in db.users.items() if match(u)]
        if len(users) == 1:
            return users[0]
        if len(users) > 1:
            raise ValueError(f"{known_info!r} names {len(users)} users")
    raise ValueError(f"{known_info!r} names no user")


class Bench(common.Bench):
    """Loaded once: τ²'s database, and the AgentTwin world made from it."""

    name = "tau2-retail"
    view = staticmethod(view)
    constructor = staticmethod(get_environment)

    def __init__(self) -> None:
        self.db = RetailDB.load(RETAIL_DB_PATH)
        super().__init__(WORLD)

    def fresh(self) -> Any:
        return get_environment(db=self.db.model_copy(deep=True))

    def session(self, task: Task) -> dict[str, str]:
        return {"user_id": session_user(self.db, task.user_scenario.instructions.known_info or "")}


__all__ = ["HERE", "WORLD", "Bench", "session_user"]
