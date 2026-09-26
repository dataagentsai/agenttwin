"""The offstage humans' contracts, asserted as documented — generation run 2
found them by calling with spies (NOTES §8), so these are those spies.

Table-driven: each row is an actor, a moment, and exactly the calls it must make.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agenttwin import Approver, Desk


@dataclass(frozen=True)
class Item:
    id: str
    created_at: int


class Queue:
    def __init__(self, *items: Item) -> None:
        self.items = list(items)

    async def pending(self) -> list[Item]:
        return list(self.items)


class Spy:
    def __init__(self, refuse: str | None = None) -> None:
        self.calls: list[tuple] = []
        self.refuse = refuse

    async def __call__(self, store, item_id, **kwargs):
        self.calls.append((store, item_id, kwargs))
        if self.refuse:
            raise RuntimeError(self.refuse)


Q = Queue(Item("A-1", created_at=100))

# [name, build(store, spy), at, expected kwargs of the one call (None: no call), outcome]
CASES = [
    (
        "approver grants once due",
        lambda s, f: Approver.grants(s, f, delay_s=30, name="ops-1"),
        130,
        {"granted": True, "by": "ops-1", "now": 130},
        "granted",
    ),
    (
        "approver denies",
        lambda s, f: Approver.denies(s, f, name="ops-1"),
        100,
        {"granted": False, "by": "ops-1", "now": 100},
        "denied",
    ),
    (
        "approver waits before created_at + delay_s",
        lambda s, f: Approver.grants(s, f, delay_s=30),
        129,
        None,
        "waiting",
    ),
    ("a silent approver never calls", lambda s, f: Approver.silent(s, f), 10**6, None, "waiting"),
    (
        "desk closes once due",
        lambda s, f: Desk.answers(s, f, delay_s=5, name="desk-1", note="done"),
        105,
        {"outcome": "resolved", "by": "desk-1", "note": "done", "now": 105},
        "resolved",
    ),
    (
        "desk disagrees",
        lambda s, f: Desk.says_agent_could_have(s, f, name="desk-1"),
        100,
        {"outcome": "agent_could_have", "by": "desk-1", "note": "", "now": 100},
        "agent_could_have",
    ),
    ("a desk that never comes", lambda s, f: Desk.never_comes(s, f), 10**6, None, "waiting"),
]


@pytest.mark.parametrize(
    ("name", "build", "at", "kwargs", "outcome"), CASES, ids=[c[0] for c in CASES]
)
async def test_an_offstage_human_calls_exactly_what_its_docstring_says(
    name, build, at, kwargs, outcome
) -> None:
    spy = Spy()
    actor = build(Q, spy)
    (seen,) = await actor.review(at=at)
    assert seen.outcome == outcome
    assert spy.calls == ([] if kwargs is None else [(Q, "A-1", kwargs)])


@pytest.mark.parametrize("build", [Approver.grants, Desk.answers], ids=["approver", "desk"])
async def test_a_rejected_decision_is_recorded_as_refused_with_its_reason(build) -> None:
    actor = build(Q, Spy(refuse="too late"))
    (seen,) = await actor.review(at=100)
    assert (seen.outcome, seen.detail) == ("refused", "too late")
