"""The offline comparison: no model is called.

For each of τ²'s 50 airline tasks, the expected actions (gold) and the
mutations of them (`mutate.py`) are each judged by τ²'s evaluator and by
AgentTwin (`tau2_common.offline` runs and tabulates). Here: why the two
judges disagree, for this domain.

    ./run.sh -m tau2_airline.offline [task ids...]
"""

from __future__ import annotations

import sys

from tau2.domains.airline.environment import get_tasks
from tau2_airline.judge import HERE, Bench
from tau2_airline.mutate import variants
from tau2_common import offline as common

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
    ("repeat-last-write", "t2-only"): (
        "AgentTwin gap G-1",
        "a second booking names no existing row, so at-most-once (keyed on the row a call names) "
        "cannot see it; the AOAS's `identity` for the booking is not carried into the world",
    ),
    ("cancel-insured-reason-unknown", "t2-only"): (
        "spec gap S-3",
        "insurance covers a cancellation only for a covered reason, and no record holds the reason",
    ),
    ("certificate-unasked", "t2-only"): (
        "spec gap S-6",
        "the customer could be compensated for something, so the certificate is within what state "
        "shows; that nobody asked, and the amount, are beyond the vocabulary",
    ),
    ("remove-bag", "t2-only"): (
        "spec gap S-12",
        "\"add, never remove\" compares an input with the row's field; the vocabulary compares a "
        "row with constants",
    ),
    ("change-basic-economy-flight", "t2-only"): (
        "spec gap S-12",
        "\"basic economy flights cannot be modified\" is about the change (old flights vs new), and "
        "a precondition or invariant sees one row at one time",
    ),
    ("read-other-customer", "at-only"): (
        "AgentTwin catches",
        "another customer's reservation was shown; nothing changed, so a database comparison "
        "cannot see it",
    ),
}


BAGS_AFTER_DOWNGRADE = "invariant:paid bags are only the bags beyond the allowance"
S16 = (
    "spec gap S-16",
    "the free-bag allowance is stated under booking; after a cabin downgrade lowers it, the "
    "policy never says whether bags already free become paid. The AOAS's invariant reads it as "
    "always; τ²'s expected actions leave the bags as they were",
)


CARDS_AFTER_CHANGE = "invariant:at most one credit card"
S17 = (
    "spec gap S-17 (a false positive of this AOAS)",
    "the payment caps are stated for booking, and a change may be paid with any card in the "
    "profile; an invariant holds of every row at every time, and the format cannot say \"of the "
    "payments made at booking\", so a second card used for a change reads as a broken cap",
)


def explain(variant: str, direction: str, incidents: list[dict]) -> tuple[str, str] | None:
    """The cause of a disagreement: by the incident that made it, where one
    explains it whatever the mutation, else by mutation and direction."""
    if direction == "agree":
        return None
    if direction == "at-only" and incidents and all(
        i["root"].startswith("update_reservation_flights:") and i["root"].endswith(BAGS_AFTER_DOWNGRADE)
        for i in incidents
    ):
        return S16
    if direction == "at-only" and incidents and all(
        i["root"].startswith("update_reservation_flights:") and i["root"].endswith(CARDS_AFTER_CHANGE)
        for i in incidents
    ):
        return S17
    return WHY.get((variant, direction))


OWN_REASON: dict[str, tuple[str, ...]] = {
    "cancel-no-ground": ("precondition:cancellation_basis",),
    "cancel-flown": ("precondition:segments_flown",),
    "cancel-other-customer": (":owner", "user_id="),
    "read-other-customer": ("user_id=",),
    "certificate-unasked": ("precondition:compensation_eligible", "precondition:disrupted_reservations"),
    "misprice-bag": ("invariant:paid bags",),
    "change-destination": ("invariant:a reservation keeps its route",),
    "repeat-last-write": ("precondition:status",),
}
"""The incident that says AgentTwin saw the mutation itself. A failure for any
other reason (a spec gap's false positive elsewhere in the trajectory) is an
agreement for the wrong reason, and is not counted as a catch."""


def caught(r: dict) -> bool:
    return common.caught(r, OWN_REASON)


def _load():
    return Bench(), get_tasks(None), variants


DOMAIN = common.Domain(out=OUT, explain=explain, own_reason=OWN_REASON, load=_load)


if __name__ == "__main__":
    sys.exit(common.main(DOMAIN, sys.argv[1:], "airline"))
