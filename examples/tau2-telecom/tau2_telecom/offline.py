"""The offline comparison: no model is called.

For each of τ²'s 114 telecom tasks in its `base` split (the split τ² runs by
default, of 2,285 in all), the expected actions (gold) — the agent's and the
customer's — and the mutations of them (`mutate.py`) are each judged by τ²'s
evaluators and by AgentTwin (`tau2_common.offline` runs and tabulates). Here:
why the two judges disagree, for this domain.

    ./run.sh -m tau2_telecom.offline [task ids...]
    ./run.sh -m tau2_telecom.offline --report     # re-tabulate results/offline.json
"""

from __future__ import annotations

import sys

from tau2.domains.telecom.environment import get_tasks
from tau2_common import offline as common
from tau2_telecom.judge import HERE, Bench
from tau2_telecom.mutate import variants

OUT = HERE / "results"

# Why the two judges disagree, by mutation and direction. Every disagreement
# the run finds is looked up here; one with no entry is printed as
# UNEXPLAINED and fails the run, so nothing is explained after the fact.
NO_RECORDS = (
    "τ² checks no records here (T-2)",
    "τ²'s basis for telecom is ENV_ASSERTION (and ACTION for some tasks), never DB: it reads the "
    "phone, and in some tasks one or two facts of the carrier's records (refuelled amount, an overdue "
    "bill paid). A write to the records that no assertion names passes. AgentTwin judges every write "
    "against the AOAS",
)
WHY: dict[tuple[str, str], tuple[str, str]] = {
    ("drop-agent-write", "t2-only"): (
        "by design",
        "AgentTwin judged the policy, not the request: no scenario states what this customer asked "
        "for, and leaving a request undone breaks no rule. τ² asserts the outcome",
    ),
    ("drop-customer-fix", "t2-only"): (
        "dual control, outside the format (T-1)",
        "the step left undone is the customer's, on their phone. The phone is not in the AOAS or the "
        "world, and AgentTwin has no customer who acts with tools; τ² asserts the phone's state",
    ),
    ("read-other-customer", "at-only"): (
        "AgentTwin catches",
        "another customer's record was shown; nothing changed, so no assertion on state can see it",
    ),
    ("refuel-over-cap", "at-only"): NO_RECORDS,
    ("refuel-other-line", "at-only"): NO_RECORDS,
    ("refuel-other-customer", "at-only"): NO_RECORDS,
    ("refuel-suspended-line", "at-only"): NO_RECORDS,
    ("payment-request-not-overdue", "at-only"): NO_RECORDS,
    ("resume-contract-ended", "at-only"): NO_RECORDS,
    ("resume-before-payment", "at-only"): NO_RECORDS,
}


OWN_REASON: dict[str, tuple[str, ...]] = {
    "read-other-customer": ("customer_id=",),
    "refuel-over-cap": ("invariant:at most 2 GB refuelled",),
    "refuel-other-line": (":owner",),
    "refuel-other-customer": (":owner",),
    "refuel-suspended-line": ("precondition:status",),
    "payment-request-not-overdue": ("precondition:status",),
    "resume-contract-ended": ("precondition:contract_ended",),
    "resume-before-payment": ("precondition:owner_overdue_bills",),
}
"""The incident that says AgentTwin saw the mutation itself."""


def explain(variant: str, direction: str, incidents: list[dict]) -> tuple[str, str] | None:
    if direction == "agree":
        return None
    return WHY.get((variant, direction))


def caught(r: dict) -> bool:
    return common.caught(r, OWN_REASON)


def _load():
    return Bench(), get_tasks("base"), variants


DOMAIN = common.Domain(out=OUT, explain=explain, own_reason=OWN_REASON, load=_load)


if __name__ == "__main__":
    sys.exit(common.main(DOMAIN, sys.argv[1:], "telecom (base split)"))
