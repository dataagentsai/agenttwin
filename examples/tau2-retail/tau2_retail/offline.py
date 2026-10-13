"""The offline comparison: no model is called.

For each of τ²'s 114 retail tasks, the expected actions (gold) and the
mutations of them (`mutate.py`) are each judged by τ²'s evaluators and by
AgentTwin (`tau2_common.offline` runs and tabulates). Here: why the two
judges disagree, for this domain.

    ./run.sh -m tau2_retail.offline [task ids...]
    ./run.sh -m tau2_retail.offline --report     # re-tabulate results/offline.json
"""

from __future__ import annotations

import sys

from tau2.domains.retail.environment import get_tasks
from tau2_common import offline as common
from tau2_retail.judge import HERE, Bench
from tau2_retail.mutate import variants

OUT = HERE / "results"

# Why the two judges disagree, by mutation and direction. Every disagreement
# the run finds is looked up here; one with no entry is printed as
# UNEXPLAINED and fails the run, so nothing is explained after the fact.
WHY: dict[tuple[str, str], tuple[str, str]] = {
    ("drop-last-write", "t2-only"): (
        "by design",
        "AgentTwin judged the policy, not the request: no scenario states what this customer asked "
        "for, and leaving a request undone breaks no rule. τ² compares with the expected database",
    ),
    ("read-other-customer", "at-only"): (
        "AgentTwin catches",
        "another customer's order was shown; nothing changed, so a database comparison cannot see it",
    ),
    ("cancel-unasked", "t2-only"): (
        "spec gap R-3",
        "the order is the customer's own, pending, and the reason is one the policy accepts: every "
        "condition state can show holds. That the customer never asked, never confirmed and never "
        "gave the reason is conversation, which no record holds. τ² compares with the expected database",
    ),
}


OWN_REASON: dict[str, tuple[str, ...]] = {
    "read-other-customer": ("user_id=",),
    "cancel-other-customer": (":owner", "precondition:user_id", "user_id="),
    "modify-other-profile": (":owner", "user_id="),
    "modify-after-items-modified": ("precondition:status",),
    "exchange-same-item": ("invariant:an exchange changes an item",),
}
"""The incident that says AgentTwin saw the mutation itself."""


def explain(variant: str, direction: str, incidents: list[dict]) -> tuple[str, str] | None:
    """The cause of a disagreement, by mutation and direction."""
    if direction == "agree":
        return None
    return WHY.get((variant, direction))


def caught(r: dict) -> bool:
    return common.caught(r, OWN_REASON)


def _load():
    return Bench(), get_tasks(None), variants


DOMAIN = common.Domain(out=OUT, explain=explain, own_reason=OWN_REASON, load=_load)


if __name__ == "__main__":
    sys.exit(common.main(DOMAIN, sys.argv[1:], "retail"))
