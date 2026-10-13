"""τ²'s airline database, as the rows the AOAS describes.

Deterministic: the same database gives the same rows. Each derived field the
AOAS declares (`derived:`) is computed here, and each is a place where a rule
the condition vocabulary cannot state — another entity, an OR, a count, some
arithmetic — lives in code instead (finding S-1).

Reads a `FlightDB` by attribute, not a dump of it, because the stand-in calls
this after every write and the database is 6.7 MB.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

NOW = datetime(2024, 5, 15, 15, 0, 0)
"""The policy's current time: "The current time is 2024-05-15 15:00:00 EST"."""

FREE_BAGS: dict[str, dict[str, int]] = {
    "regular": {"basic_economy": 0, "economy": 1, "business": 2},
    "silver": {"basic_economy": 1, "economy": 2, "business": 3},
    "gold": {"basic_economy": 2, "economy": 3, "business": 4},
}
"""Free checked bags per passenger, from the policy's table."""

FLOWN = {"flying", "landed"}
DISRUPTED = {"cancelled", "delayed"}
SYSTEM_ENTITIES = ("user", "payment_method", "reservation", "flight", "airport")
"""What τ²'s database holds. `transfer` rows are the desk's, kept by the stand-in."""


def flight_key(flight_number: str, date: str) -> str:
    return f"{flight_number}/{date}"


def _status(db: Any, flight_number: str, date: str) -> str | None:
    flight = db.flights.get(flight_number)
    if flight is None or date not in flight.dates:
        return None
    return flight.dates[date].status


def _source(payment_id: str) -> str:
    return payment_id.rsplit("_", 1)[0]


def _route_consistent(r: Any) -> bool:
    legs = r.flights
    if not legs or legs[0].origin != r.origin:
        return False
    if r.flight_type == "one_way":
        return legs[-1].destination == r.destination
    return any(leg.destination == r.destination for leg in legs) and legs[-1].destination == r.origin


def cancellation_basis(r: Any, statuses: Iterable[str | None]) -> str:
    """The first of the policy's cancellation grounds the record shows.

    The fourth ground is "insured, and the reason is covered by insurance";
    no record holds the reason, so `insured` is only the first half (S-3)."""
    if (NOW - datetime.fromisoformat(r.created_at)).total_seconds() <= 24 * 3600:
        return "booked_within_24h"
    if any(s == "cancelled" for s in statuses):
        return "airline_cancelled"
    if r.cabin == "business":
        return "business"
    if r.insurance == "yes":
        return "insured"
    return "none"


def reservation_row(db: Any, r: Any) -> dict[str, Any]:
    statuses = [_status(db, leg.flight_number, leg.date) for leg in r.flights]
    owner = db.users.get(r.user_id)
    passengers = len(r.passengers)
    allowance = FREE_BAGS[owner.membership][r.cabin] * passengers if owner else 0
    used: dict[str, set[str]] = {"certificate": set(), "credit_card": set(), "gift_card": set()}
    for p in r.payment_history:
        used.setdefault(_source(p.payment_id), set()).add(p.payment_id)
    return {
        "reservation_id": r.reservation_id,
        "user_id": r.user_id,
        "origin": r.origin,
        "destination": r.destination,
        "flight_type": r.flight_type,
        "cabin": r.cabin,
        "insurance": r.insurance,
        "status": r.status or "active",
        "created_at": r.created_at,
        "total_baggages": r.total_baggages,
        "nonfree_baggages": r.nonfree_baggages,
        "passenger_count": passengers,
        "net_paid": sum(p.amount for p in r.payment_history),
        "hours_since_booking": int((NOW - datetime.fromisoformat(r.created_at)).total_seconds() // 3600),
        "segments_flown": sum(s in FLOWN for s in statuses),
        "segments_cancelled": sum(s == "cancelled" for s in statuses),
        "cancellation_basis": cancellation_basis(r, statuses),
        "paid_bags_match_allowance": r.nonfree_baggages == max(0, r.total_baggages - allowance),
        "route_consistent": _route_consistent(r),
        "certificates_used": len(used["certificate"]),
        "credit_cards_used": len(used["credit_card"]),
        "gift_cards_used": len(used["gift_card"]),
    }


def user_row(db: Any, u: Any) -> dict[str, Any]:
    held = [db.reservations[rid] for rid in u.reservations if rid in db.reservations]
    disrupted = sum(
        1
        for r in held
        if any(_status(db, leg.flight_number, leg.date) in DISRUPTED for leg in r.flights)
    )
    return {
        "user_id": u.user_id,
        "email": u.email,
        "dob": u.dob,
        "membership": u.membership,
        "compensation_eligible": u.membership in ("silver", "gold")
        or any(r.insurance == "yes" or r.cabin == "business" for r in held),
        "disrupted_reservations": disrupted,
    }


def rows(db: Any, airports: Iterable[Any] = ()) -> dict[str, tuple[dict[str, Any], ...]]:
    """Every row of every entity τ²'s database holds, keyed as the AOAS keys them."""
    return {
        "user": tuple(user_row(db, u) for u in db.users.values()),
        "payment_method": tuple(
            {
                "id": pm.id,
                "user_id": u.user_id,
                "source": pm.source,
                "amount": getattr(pm, "amount", None),
            }
            for u in db.users.values()
            for pm in u.payment_methods.values()
        ),
        "reservation": tuple(reservation_row(db, r) for r in db.reservations.values()),
        "flight": tuple(
            {
                "id": flight_key(f.flight_number, date),
                "flight_number": f.flight_number,
                "date": date,
                "origin": f.origin,
                "destination": f.destination,
                "status": d.status,
            }
            for f in db.flights.values()
            for date, d in f.dates.items()
        ),
        "airport": tuple({"iata": a.iata, "city": a.city} for a in airports),
    }
