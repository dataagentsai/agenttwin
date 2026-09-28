"""`named_contradictions` — every record a reply names, judged against the world.

Table-driven, on the lending library. The subject pattern is written in a
shop's nouns (order, parcel, it), so these replies use *it* — which is itself a
finding: a library's "loan L-1 is returned" is not a claim this oracle can see.
"""

from __future__ import annotations

import copy

import pytest

from agenttwin import Live, load
from agenttwin.truth import named_contradictions
from tests.test_loader import SPEC, WORLD, write


def library(tmp_path) -> Live:
    world = copy.deepcopy(WORLD)
    world["records"]["loan"].append(
        {"id": "L-2", "member_id": "M-1", "status": "open", "days_overdue": 0}
    )
    return Live.start(load(write(tmp_path, SPEC, world)))


# [name, reply, what L-1 held when the turn began (None: as now), keys reported]
CASES = [
    ("a false claim about a named record", "L-1: it is renewed.", None, ["L-1"]),
    ("a true claim", "L-1: it is open.", None, []),
    ("each key judged on its own stretch", "L-1: it is renewed. L-2: it is open.", None, ["L-1"]),
    ("the second key, not the first", "L-1: it is open. L-2: it is returned.", None, ["L-2"]),
    ("a claim naming no record", "It is renewed.", None, []),
    ("a rule, not a report", "L-1: it cannot be renewed once overdue.", None, []),
    ("what it held when the turn began is stale, not false", "L-1: it is renewed.", "renewed", []),
    ("a key inside another word is not a mention", "XL-1: it is renewed.", None, []),
]


@pytest.mark.parametrize("name,reply,held,expected", CASES, ids=[c[0] for c in CASES])
def test_a_reply_is_judged_on_the_records_it_names(tmp_path, name, reply, held, expected):
    live = library(tmp_path)
    before = live.snapshot()
    if held is not None:
        before["loan"]["L-1"]["status"] = held
    found = named_contradictions(live, reply, held=before)
    assert [key for _, key, _ in found] == expected
