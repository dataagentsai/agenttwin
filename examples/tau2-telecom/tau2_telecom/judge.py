"""One trajectory, two judges — the telecom bench over the shared judges.

`tau2_common.judge` says what each judge does. Here: τ²'s telecom databases
(the carrier's records, and the customer's phone), the AgentTwin world made
from the first, and who the customer is.

**Who the customer is.** The task's own words for the customer
(`known_info`) give a phone number; the session is the customer τ²'s records
find for it (as `get_customer_by_phone` finds it), with that phone number,
because the agent's refuelling and roaming act on the line of "the phone
number the user provided". Nothing here reads the expected actions.

**The customer's own tools** run on τ²'s environment as the customer would
(`Standin.customer_acts`), never over the agent's surface, and are not judged
by AgentTwin: they are not the agent's acts (finding T-1).

**Determinism.** τ²'s telecom code names a new bill with `uuid4` (a refuel
for a customer with no draft bill). Bills are numbered from a counter here
instead, so two runs give the same rows; nothing else changes.
"""

from __future__ import annotations

import itertools
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import tau2.domains.telecom.tools as telecom_tools
from tau2.data_model.tasks import Task
from tau2.domains.telecom.data_model import TelecomDB
from tau2.domains.telecom.environment import get_environment
from tau2.domains.telecom.user_data_model import TelecomUserDB
from tau2.domains.telecom.utils import TELECOM_DB_PATH, TELECOM_USER_DB_PATH
from tau2_common import judge as common
from tau2_telecom.convert import view

HERE = Path(__file__).resolve().parents[1]
WORLD = HERE / "world.yaml"

_PHONE = re.compile(r"\b(\d{3}-\d{3}-\d{4})\b")
_BILLS = itertools.count(1)
telecom_tools.uuid = SimpleNamespace(uuid4=lambda: uuid.UUID(int=next(_BILLS)))


def session_of(db: TelecomDB, known_info: str) -> dict[str, str]:
    found = _PHONE.search(known_info)
    if not found:
        raise ValueError(f"{known_info!r} gives no phone number")
    phone = found.group(1)
    for c in db.customers:
        lines = {ln.phone_number: ln for ln in db.lines if ln.line_id in c.line_ids}
        if c.phone_number == phone or phone in lines:
            return {"customer_id": c.customer_id, "phone_number": phone}
    raise ValueError(f"no customer has {phone}")


class Bench(common.Bench):
    """Loaded once: τ²'s databases, and the AgentTwin world made from the carrier's."""

    name = "tau2-telecom"
    view = staticmethod(view)
    constructor = staticmethod(get_environment)

    def __init__(self) -> None:
        self.db = TelecomDB.load(TELECOM_DB_PATH)
        self.user_db = TelecomUserDB.load(TELECOM_USER_DB_PATH)
        super().__init__(WORLD)

    def fresh(self) -> Any:
        return get_environment(db=self.db.model_copy(deep=True), user_db=self.user_db.model_copy(deep=True))

    def session(self, task: Task) -> dict[str, str]:
        return session_of(self.db, task.user_scenario.instructions.known_info or "")


__all__ = ["HERE", "WORLD", "Bench", "session_of"]
