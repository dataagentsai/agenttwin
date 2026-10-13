"""τ²'s telecom database (the carrier's records), as the rows the AOAS describes.

Deterministic: the same database gives the same rows. Three derived fields:
a line's owner (the records list lines on the customer), whether its contract
has ended, and its owner's overdue bills — each a fact about another row the
condition vocabulary cannot reach (airline S-1).

The customer's phone (τ²'s user database) is not converted: it is outside
the AOAS and the world (finding T-1).
"""

from __future__ import annotations

from datetime import date
from typing import Any

TODAY = date(2025, 2, 25)
"""The policy's current date: "The current time is 2025-02-25 12:08:00 EST"."""


def _v(value: Any) -> Any:
    """An enum's word, a date's ISO form, anything else as it is."""
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    return value


def rows(db: Any) -> dict[str, tuple[dict[str, Any], ...]]:
    """Every row of every entity the carrier's records hold, keyed as the AOAS keys them."""
    bills = {b.bill_id: b for b in db.bills}
    overdue = {
        c.customer_id: sum(1 for b in c.bill_ids if b in bills and _v(bills[b].status) == "Overdue")
        for c in db.customers
    }
    owner = {line_id: c.customer_id for c in db.customers for line_id in c.line_ids}
    return {
        "customer": tuple(
            {
                "customer_id": c.customer_id,
                "full_name": c.full_name,
                "date_of_birth": c.date_of_birth,
                "phone_number": c.phone_number,
                "account_status": _v(c.account_status),
                "overdue_bills": overdue[c.customer_id],
            }
            for c in db.customers
        ),
        "line": tuple(
            {
                "line_id": ln.line_id,
                **({"customer_id": owner[ln.line_id]} if ln.line_id in owner else {}),
                "phone_number": ln.phone_number,
                "status": _v(ln.status),
                "plan_id": ln.plan_id,
                "roaming_enabled": ln.roaming_enabled,
                "data_used_gb": ln.data_used_gb,
                "data_refueling_gb": ln.data_refueling_gb,
                "contract_end_date": _v(ln.contract_end_date),
                "contract_ended": ln.contract_end_date is not None and ln.contract_end_date < TODAY,
                "owner_overdue_bills": overdue.get(owner.get(ln.line_id, ""), 0),
            }
            for ln in db.lines
        ),
        "bill": tuple(
            {"bill_id": b.bill_id, "customer_id": b.customer_id, "status": _v(b.status), "total_due": b.total_due}
            for b in db.bills
        ),
        "plan": tuple(
            {
                "plan_id": p.plan_id,
                "name": p.name,
                "data_limit_gb": p.data_limit_gb,
                "price_per_month": p.price_per_month,
                "data_refueling_price_per_gb": p.data_refueling_price_per_gb,
            }
            for p in db.plans
        ),
        "device": tuple({"device_id": d.device_id} for d in db.devices),
    }


def view(env: Any) -> dict[str, tuple[dict[str, Any], ...]]:
    return rows(env.tools.db)
