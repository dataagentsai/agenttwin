"""The τ²-bench airline example: converter, stand-in and judges.

Needs τ²-bench importable, so it runs through `./run.sh -m pytest tests`; the
repository's own suite skips it. Table-driven throughout.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("tau2")

from tau2.domains.airline.data_model import FlightDB  # noqa: E402
from tau2.domains.airline.environment import get_tasks  # noqa: E402

from tau2_airline.convert import reservation_row, user_row  # noqa: E402
from tau2_airline.judge import Bench, agenttwin, differences, session_user, tau2  # noqa: E402
from tau2_airline.mutate import gold  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
FAMILY = HERE.parents[2] / "clean-ai-engineering"


@pytest.fixture(scope="module")
def bench() -> Bench:
    return Bench()


# ------------------------------------------------------------------ the spec


@pytest.mark.skipif(
    not (FAMILY / "tools/validate-aoas.js").exists() or shutil.which("node") is None,
    reason="needs clean-ai-engineering checked out beside agenttwin, and node",
)
def test_the_aoas_validates() -> None:
    done = subprocess.run(
        ["node", str(FAMILY / "tools/validate-aoas.js"), str(HERE / "tau2-airline.aoas.yaml")],
        capture_output=True,
        text=True,
        cwd=FAMILY,
    )
    assert done.returncode == 0, done.stdout + done.stderr


# --------------------------------------------------------------- the converter


def _db(reservation: dict[str, Any], membership: str = "regular", statuses=("available",)) -> FlightDB:
    flights = {
        f"HAT{i:03d}": {
            "flight_number": f"HAT{i:03d}",
            "origin": leg["origin"],
            "destination": leg["destination"],
            "scheduled_departure_time_est": "06:00:00",
            "scheduled_arrival_time_est": "09:00:00",
            "dates": {leg["date"]: _date_status(status)},
        }
        for i, (leg, status) in enumerate(zip(reservation["flights"], statuses, strict=True), start=1)
    }
    for i, leg in enumerate(reservation["flights"], start=1):
        leg["flight_number"] = f"HAT{i:03d}"
    return FlightDB.model_validate(
        {
            "flights": flights,
            "users": {
                "ann_lee_1000": {
                    "user_id": "ann_lee_1000",
                    "name": {"first_name": "Ann", "last_name": "Lee"},
                    "address": {"address1": "1 A St", "city": "X", "country": "USA", "state": "NY", "zip": "10001"},
                    "email": "ann@example.org",
                    "dob": "1990-01-01",
                    "payment_methods": {},
                    "saved_passengers": [],
                    "membership": membership,
                    "reservations": ["ABC123"],
                }
            },
            "reservations": {"ABC123": reservation},
        }
    )


def _date_status(status: str) -> dict[str, Any]:
    if status == "available":
        return {"status": status, "available_seats": {"basic_economy": 9, "economy": 9, "business": 9},
                "prices": {"basic_economy": 50, "economy": 100, "business": 300}}
    if status in ("landed",):
        return {"status": status, "actual_departure_time_est": "2024-05-10T06:00:00",
                "actual_arrival_time_est": "2024-05-10T09:00:00"}
    if status in ("delayed", "on time"):
        return {"status": status, "estimated_departure_time_est": "2024-05-16T06:00:00",
                "estimated_arrival_time_est": "2024-05-16T09:00:00"}
    return {"status": status}


def _reservation(**over: Any) -> dict[str, Any]:
    r = {
        "reservation_id": "ABC123", "user_id": "ann_lee_1000", "origin": "JFK", "destination": "LAX",
        "flight_type": "one_way", "cabin": "economy",
        "flights": [{"origin": "JFK", "destination": "LAX", "date": "2024-05-20", "price": 100}],
        "passengers": [{"first_name": "Ann", "last_name": "Lee", "dob": "1990-01-01"}],
        "payment_history": [{"payment_id": "credit_card_1234567", "amount": 100}],
        "created_at": "2024-05-01T10:00:00", "total_baggages": 1, "nonfree_baggages": 0, "insurance": "no",
    }
    r.update(over)
    return r


# name, reservation overrides, membership, flight statuses, expected derived fields
DERIVED = [
    ("no ground", {}, "regular", ("available",), {"cancellation_basis": "none", "segments_flown": 0}),
    ("booked within 24 hours", {"created_at": "2024-05-14T16:00:00"}, "regular", ("available",),
     {"cancellation_basis": "booked_within_24h", "hours_since_booking": 23}),
    ("a day and an hour ago", {"created_at": "2024-05-14T14:00:00"}, "regular", ("available",),
     {"cancellation_basis": "none", "hours_since_booking": 25}),
    ("the airline cancelled a flight", {}, "regular", ("cancelled",),
     {"cancellation_basis": "airline_cancelled", "segments_cancelled": 1}),
    ("business", {"cabin": "business", "total_baggages": 2}, "regular", ("available",),
     {"cancellation_basis": "business", "paid_bags_match_allowance": True}),
    ("insured", {"insurance": "yes"}, "regular", ("available",), {"cancellation_basis": "insured"}),
    ("flown", {}, "regular", ("landed",), {"segments_flown": 1}),
    ("a free bag charged", {"nonfree_baggages": 1}, "regular", ("available",), {"paid_bags_match_allowance": False}),
    ("gold's allowance", {"total_baggages": 3}, "gold", ("available",), {"paid_bags_match_allowance": True}),
    ("a one-way that lands elsewhere", {"destination": "SFO"}, "regular", ("available",), {"route_consistent": False}),
    ("a round trip home", {"flight_type": "round_trip", "flights": [
        {"origin": "JFK", "destination": "LAX", "date": "2024-05-20", "price": 100},
        {"origin": "LAX", "destination": "JFK", "date": "2024-05-22", "price": 100}]},
     "regular", ("available", "available"), {"route_consistent": True}),
    ("two credit cards", {"payment_history": [{"payment_id": "credit_card_1", "amount": 50},
                                               {"payment_id": "credit_card_2", "amount": 50}]},
     "regular", ("available",), {"credit_cards_used": 2}),
]


@pytest.mark.parametrize(("name", "over", "membership", "statuses", "expected"), DERIVED, ids=[d[0] for d in DERIVED])
def test_a_derived_field_says_what_the_policy_says(name, over, membership, statuses, expected) -> None:
    db = _db(_reservation(**over), membership, statuses)
    row = reservation_row(db, db.reservations["ABC123"])
    assert {k: row[k] for k in expected} == expected


# name, membership, reservation overrides, statuses, expected user fields
ELIGIBLE = [
    ("regular, economy, uninsured", "regular", {}, ("delayed",), {"compensation_eligible": False, "disrupted_reservations": 1}),
    ("silver", "silver", {}, ("available",), {"compensation_eligible": True, "disrupted_reservations": 0}),
    ("regular but insured", "regular", {"insurance": "yes"}, ("cancelled",), {"compensation_eligible": True, "disrupted_reservations": 1}),
]


@pytest.mark.parametrize(("name", "membership", "over", "statuses", "expected"), ELIGIBLE, ids=[e[0] for e in ELIGIBLE])
def test_who_may_be_compensated(name, membership, over, statuses, expected) -> None:
    db = _db(_reservation(**over), membership, statuses)
    row = user_row(db, db.users["ann_lee_1000"])
    assert {k: row[k] for k in expected} == expected


def test_every_row_of_tau2_s_database_is_one_the_spec_says_can_exist(bench: Bench) -> None:
    """`with_records` refuses a row outside an enum, breaking an invariant or
    joining to nothing; τ²'s 2,000 reservations all load."""
    assert {e: len(r) for e, r in bench.world.records.items()} == {
        "user": 500, "payment_method": 1486, "reservation": 2000, "flight": 9000, "airport": 20,
    }


# ------------------------------------------------------------ the two judges

TASKS = {t.id: t for t in get_tasks(None)}

# name, task (for the customer and τ²'s criteria), steps, AgentTwin's incident roots, τ² passes
TRAJECTORIES = [
    ("gold, a refusal with no actions", "0", None, [], True),
    ("gold, two cancellations", "7", None, [], True),
    ("a cancellation on no ground", "0",
     [("call", "cancel_reservation", {"reservation_id": "EHGLP3"})],
     ["cancel_reservation:EHGLP3:precondition:cancellation_basis"], False),
    ("cancelling twice", "7", "repeat",
     ["cancel_reservation:59XX6W", "cancel_reservation:59XX6W:precondition:status"], False),
    ("another customer's reservation, read", "0",
     [("call", "get_reservation_details", {"reservation_id": "4WQ150"})],
     ["reservation:4WQ150:user_id=emma_kim_9957"], True),
    ("a refused call lands nothing", "0",
     [("call", "update_reservation_passengers", {"reservation_id": "EHGLP3", "passengers": []})], [], True),
    ("a call that never existed", "0", [("call", "calculate", {"expression": "2 + 2"})], [], True),
]


@pytest.mark.parametrize(("name", "task_id", "steps", "roots", "tau2_passes"), TRAJECTORIES, ids=[t[0] for t in TRAJECTORIES])
async def test_both_judges(bench: Bench, name, task_id, steps, roots, tau2_passes) -> None:
    task = TASKS[task_id]
    if steps is None:
        steps = gold(task)
    elif steps == "repeat":
        steps = [*gold(task), [s for s in gold(task) if s[0] == "call"][-1]]
    ours = await agenttwin(bench, session_user(task), steps)
    theirs = tau2(bench, task, steps)
    assert sorted(i["root"] for i in ours.incidents) == sorted(roots)
    assert theirs.passed is tau2_passes
    assert differences(ours.answers, theirs.answers) == [], "the stand-in answered as τ²'s environment does"
