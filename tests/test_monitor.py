"""The world as a monitor, and the calendar that makes a record lie.

Table-driven, on the lending library from `test_loader`, so the monitor is
shown to know no domain: four watches, asked as each call lands and each reply
goes out, on a world whose records are loans.

Each row of `DAYS` is a short day — calls, replies, calendar events and clock
ticks in order — and the incidents it must raise, as `(rule, root, count)`.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client

from agenttwin import (
    INCIDENT_FORMAT,
    SESSION_META,
    CalendarEvent,
    Live,
    Moment,
    Monitor,
    default_watches,
    fire,
    load,
    project,
    with_records,
)
from agenttwin.loader import InvalidWorld
from agenttwin.monitor import ActionSeen, OwnRowsOnly
from agenttwin.projection import IncoherentWorld
from agenttwin.world import Condition
from tests.test_loader import SPEC, WORLD, write

OWNED = {"field": "loan.member_id", "equals_session": "member_id"}
DUE_S = 3600


def library(tmp_path: Path) -> Live:
    spec, world = copy.deepcopy(SPEC), copy.deepcopy(WORLD)
    spec["operations"]["list_loans"] = {
        "entity": "loan",
        "side_effect": "read",
        "input": [],
        "output": "loan[]",
        "preconditions": [OWNED],
        "authority": "agent",
    }
    spec["operations"]["stamp"] = {
        "entity": "loan",
        "side_effect": "irreversible",
        "input": ["id"],
        "preconditions": [OWNED],
        "effect": {"due_note": "stamped"},
        "authority": "agent",
    }
    spec["operations"]["renew"]["owed_when"] = [{"field": "days_overdue", "at_least": 5}]
    spec["external"]["catalogue"]["operations"] += ["list_loans", "stamp"]
    world["records"]["member"].append({"id": "M-2", "email": "n@example.org"})
    world["records"]["loan"][0]["days_overdue"] = 0  # so a scanner may renew it
    world["records"]["loan"] += [
        {"id": "L-2", "member_id": "M-1", "status": "open", "days_overdue": 5},
        {"id": "L-3", "member_id": "M-2", "status": "open", "days_overdue": 5},
    ]
    return Live.start(load(write(tmp_path, spec, world)))


SCANNER = CalendarEvent(
    id="scanner-renews-l1",
    at_s=0,
    entity="loan",
    where=(Condition(field="status", equals=("open",)), Condition(field="days_overdue", at_most=2)),
    sets={"status": "renewed"},
    record_only=True,
    by="returns scanner",
)
REALLY = SCANNER.model_copy(update={"id": "really-renewed", "record_only": False})


# The claim grammar reads a key of three or more digits as part of the subject
# (`order AB-10003 is shipped`); these keys are shorter, so replies name the key
# and then the loan.
#
# A step is one of:
#   ("call", member, tool, arguments)
#   ("say", member, said, reply)
#   ("reply-mid-turn", member, said, event, reply) — an event fires inside the turn
#   ("fire", event)
#   ("tick", seconds)
DAYS: list[tuple[str, list[tuple[Any, ...]], list[tuple[str, str, int]]]] = [
    (
        "a clean exchange raises nothing",
        [
            ("call", "M-1", "get_loan", {"id": "L-1"}),
            ("say", "M-1", "where is L-1", "For L-1, the loan is open."),
        ],
        [],
    ),
    (
        "a reply false of the record",
        [("say", "M-1", "where is L-1", "For L-1, the loan is returned.")],
        [("reply-true", "loan:L-1:status", 1)],
    ),
    (
        "a reply repeating a record the calendar made lie",
        [("fire", SCANNER), ("say", "M-1", "where is L-1", "For L-1, the loan is renewed.")],
        [("reply-true", "calendar:scanner-renews-l1", 1)],
    ),
    (
        "the same root twice is one incident, met twice",
        [
            ("fire", SCANNER),
            ("say", "M-1", "where is L-1", "For L-1, the loan is renewed."),
            ("say", "M-1", "are you sure?", "Yes: for L-1, the loan is renewed."),
        ],
        [("reply-true", "calendar:scanner-renews-l1", 2)],
    ),
    (
        "a record that really changed is not a lie",
        [("fire", REALLY), ("say", "M-1", "where is L-1", "For L-1, the loan is renewed.")],
        [],
    ),
    (
        "a claim that was true when the turn began is stale, not invented",
        [("reply-mid-turn", "M-1", "where is L-1", REALLY, "For L-1, the loan is open.")],
        [],
    ),
    (
        "another member's loan named in a reply",
        [("say", "M-1", "where are my loans", "For L-3, the loan is open.")],
        [("own-rows-only", "loan:L-3:member_id=M-1", 1)],
    ),
    (
        "a loan the member named first is not a leak",
        [("say", "M-1", "what about L-3?", "I cannot find L-3 among your loans.")],
        [],
    ),
    (
        "a listing shows only the caller's rows",
        [("call", "M-1", "list_loans", {})],
        [],
    ),
    (
        "an irreversible effect landing twice",
        [("call", "M-1", "stamp", {"id": "L-1"}), ("call", "M-1", "stamp", {"id": "L-1"})],
        [("at-most-once", "stamp:L-1", 1)],
    ),
    (
        "owed and done in time",
        [
            ("tick", 0),
            ("call", "M-1", "renew", {"id": "L-2"}),
            ("call", "M-2", "renew", {"id": "L-3"}),
            ("tick", DUE_S + 1),
        ],
        [],
    ),
    (
        "owed and left past its due time, seen twice",
        [("tick", 0), ("tick", DUE_S), ("tick", DUE_S + 60)],
        [("owed-overdue", "renew:L-2", 2), ("owed-overdue", "renew:L-3", 2)],
    ),
]


async def run_day(live: Live, steps: list[tuple[Any, ...]]) -> Monitor:
    monitor = Monitor(
        live,
        watches=default_watches(owed_within_s=DUE_S),
        session_of=lambda member: {"member_id": member},
    )
    srv = project(live, wrap=monitor.wrap, unknown_record="result")
    turn = 0
    for step in steps:
        kind = step[0]
        if kind == "fire":
            fire(live, step[1])
        elif kind == "tick":
            monitor.tick(step[1])
        elif kind == "call":
            _, member, tool, arguments = step
            async with Client(srv) as client:
                await client.call_tool(tool, arguments, meta={SESSION_META: {"member_id": member}})  # type: ignore[arg-type]
        else:
            turn += 1
            member, said = step[1], step[2]
            monitor.begin(
                Moment(
                    at=turn * 60, customer_id=member, turn_id=f"t{turn}", trace_id=f"{turn:032x}"
                ),
                said,
            )
            if kind == "reply-mid-turn":
                fire(live, step[3])
            monitor.replied(step[-1])
    return monitor


@pytest.mark.parametrize(("name", "steps", "expected"), DAYS, ids=[d[0] for d in DAYS])
async def test_the_world_raises_what_it_sees(tmp_path: Path, name, steps, expected) -> None:
    monitor = await run_day(library(tmp_path), steps)
    raised = sorted((i.rule, i.root, len(i.occurrences)) for i in monitor.incidents)
    assert raised == sorted(expected)


async def test_an_incident_carries_its_evidence(tmp_path: Path) -> None:
    monitor = await run_day(
        library(tmp_path),
        [("fire", SCANNER), ("say", "M-1", "where is L-1", "For L-1, the loan is renewed.")],
    )
    [record] = monitor.report()
    assert record["format"] == INCIDENT_FORMAT
    assert record["id"] == "INC-0001"
    assert record["rule"] == "reply-true"
    assert record["customer_id"] == "M-1"
    assert record["subject"] == {"entity": "loan", "key": "L-1"}
    assert record["turn_ids"] == ["t1"] and record["trace_ids"] == [f"{1:032x}"]
    assert record["excerpt"] == {"said": "where is L-1", "reply": "For L-1, the loan is renewed."}
    assert record["world"]["before"]["record"]["status"] == "renewed"
    assert record["world"]["before"]["truth"]["status"] == "open"
    assert "scanner-renews-l1" in record["detail"]
    assert record["first_at"] == record["last_at"] == 60 and record["count"] == 1


def test_a_leaking_answer_is_seen(tmp_path: Path) -> None:
    """The projection never leaks, so the watch is shown one that does."""
    live = library(tmp_path)
    _, action = live.world.action("list_loans")  # type: ignore[misc]
    leaked = {"found": True, "items": [live.get("loan", "L-1"), live.get("loan", "L-3")]}
    seen = ActionSeen(
        live=live,
        moment=None,
        operation="list_loans",
        action=action,
        arguments={},
        answer=leaked,
        session={"member_id": "M-1"},
    )
    assert [v.root for v in OwnRowsOnly().after_action(seen)] == ["loan:L-3:member_id=M-1"]


def test_the_monitor_must_be_told_a_turn_began(tmp_path: Path) -> None:
    monitor = Monitor(library(tmp_path), watches=default_watches(owed_within_s=DUE_S))
    with pytest.raises(RuntimeError, match="before Monitor.begin"):
        monitor.replied("hello")


# ------------------------------------------------------------------ calendar

FIRES = [
    # name, event, keys changed, (L-1's record status, its true status)
    ("record only: the record lies", SCANNER, ("L-1",), ("renewed", "open")),
    ("a real change: record and truth together", REALLY, ("L-1",), ("renewed", "renewed")),
    (
        "nothing matches: nothing changes",
        SCANNER.model_copy(update={"where": (Condition(field="status", equals=("returned",)),)}),
        (),
        ("open", "open"),
    ),
]


@pytest.mark.parametrize(("name", "event", "keys", "status"), FIRES, ids=[f[0] for f in FIRES])
def test_firing_an_event(tmp_path: Path, name, event, keys, status) -> None:
    live = library(tmp_path)
    assert fire(live, event) == keys
    assert (live.get("loan", "L-1")["status"], live.truth()["loan"]["L-1"]["status"]) == status


def test_pick_is_chosen_by_seed(tmp_path: Path) -> None:
    some = SCANNER.model_copy(update={"where": (), "pick": 1, "sets": {"due_note": "x"}})
    chosen = {fire(library(tmp_path), some, seed=seed) for seed in range(20)}
    assert {fire(library(tmp_path), some, seed=3) for _ in range(3)} == {
        fire(library(tmp_path), some, seed=3)
    }
    assert len(chosen) > 1, "different seeds reach different rows"
    assert all(len(keys) == 1 for keys in chosen)


async def test_a_write_on_the_field_settles_the_lie(tmp_path: Path) -> None:
    live = library(tmp_path)
    fire(live, SCANNER.model_copy(update={"sets": {"due_note": "lost"}}))
    assert live.cause("loan", "L-1", "due_note") == "scanner-renews-l1"
    async with Client(project(live)) as client:
        await client.call_tool(
            "note_due_date",
            {"id": "L-1", "note": "found"},
            meta={SESSION_META: {"member_id": "M-1"}},
        )  # type: ignore[arg-type]
    assert live.cause("loan", "L-1", "due_note") is None
    assert live.truth()["loan"]["L-1"]["due_note"] == "found"


def test_an_event_cannot_make_an_impossible_row(tmp_path: Path) -> None:
    live = library(tmp_path)
    returned = SCANNER.model_copy(
        update={
            "where": (Condition(field="days_overdue", at_least=5),),
            "sets": {"status": "returned"},
        }
    )
    with pytest.raises(IncoherentWorld, match="only an open loan is overdue"):
        fire(live, returned)


# --------------------------------------------------------------- population

POPULATIONS = [
    (
        "a new member and loan",
        {
            "member": ({"id": "M-9", "email": "x@example.org"},),
            "loan": ({"id": "L-9", "member_id": "M-9", "status": "open", "days_overdue": 0},),
        },
        None,
    ),
    (
        "a loan for nobody",
        {"loan": ({"id": "L-9", "member_id": "M-404", "status": "open", "days_overdue": 0},)},
        "matches no member",
    ),
    (
        "a loan outside its states",
        {"loan": ({"id": "L-9", "member_id": "M-1", "status": "lost", "days_overdue": 0},)},
        "is not one of",
    ),
    (
        "a loan that cannot exist",
        {"loan": ({"id": "L-9", "member_id": "M-1", "status": "returned", "days_overdue": 3},)},
        "only an open loan is overdue",
    ),
]


@pytest.mark.parametrize(("name", "rows", "error"), POPULATIONS, ids=[p[0] for p in POPULATIONS])
def test_a_population_is_checked_like_a_world_file(tmp_path: Path, name, rows, error) -> None:
    world = load(write(tmp_path, SPEC, WORLD))
    if error is None:
        grown = with_records(world, rows)
        assert {r["id"] for r in grown.records["loan"]} == {"L-1", "L-9"}
        assert world.records["loan"][0]["id"] == "L-1", "the original is untouched"
        return
    with pytest.raises(InvalidWorld, match=error):
        with_records(world, rows)
