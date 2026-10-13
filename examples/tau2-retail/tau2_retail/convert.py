"""τ²'s retail database, as the rows the AOAS describes.

Deterministic: the same database gives the same rows. Each derived field the
AOAS declares (`derived:`) is computed here. Two of them, against airline's
thirteen: the retail policy's rules are mostly about one order's status, which
the condition vocabulary states directly.

Reads a `RetailDB` by attribute, because the stand-in calls this after every
write.
"""

from __future__ import annotations

from typing import Any

STATUS = {"pending (item modified)": "pending (items modified)"}
"""The system's word for a state → the AOAS's (finding R-5: the policy spells it
'pending (items modifed)', the system 'pending (item modified)')."""


def order_row(o: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "order_id": o.order_id,
        "user_id": o.user_id,
        "status": STATUS.get(o.status, o.status),
        "item_count": len(o.items),
        "exchange_changes_items": not (
            o.exchange_items is not None and sorted(o.exchange_items) == sorted(o.exchange_new_items or [])
        ),
    }
    if o.cancel_reason is not None:  # an enum holds no "none"
        row["cancel_reason"] = o.cancel_reason
    return row


def rows(db: Any) -> dict[str, tuple[dict[str, Any], ...]]:
    """Every row of every entity τ²'s database holds, keyed as the AOAS keys them."""
    return {
        "user": tuple(
            {"user_id": u.user_id, "email": u.email, "zip": u.address.zip} for u in db.users.values()
        ),
        "payment_method": tuple(
            {
                "id": pm.id,
                "user_id": u.user_id,
                "source": pm.source,
                "balance": getattr(pm, "balance", None),
            }
            for u in db.users.values()
            for pm in u.payment_methods.values()
        ),
        "order": tuple(order_row(o) for o in db.orders.values()),
        "product": tuple({"product_id": p.product_id, "name": p.name} for p in db.products.values()),
        "item": tuple(
            {"item_id": v.item_id, "product_id": p.product_id, "available": v.available, "price": v.price}
            for p in db.products.values()
            for v in p.variants.values()
        ),
    }


def view(env: Any) -> dict[str, tuple[dict[str, Any], ...]]:
    return rows(env.tools.db)
