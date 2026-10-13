"""The τ²-bench retail example: converter, session, stand-in and judges.

Needs τ²-bench importable, so it runs through `./run.sh -m pytest tests`; the
repository's own suite skips it. Table-driven throughout.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("tau2")

from tau2.domains.retail.environment import get_tasks  # noqa: E402

from tau2_common.judge import agenttwin, differences, tau2  # noqa: E402
from tau2_common.mutate import gold  # noqa: E402
from tau2_retail.convert import order_row  # noqa: E402
from tau2_retail.judge import Bench, session_user  # noqa: E402
from tau2_retail.offline import caught, explain  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
FAMILY = HERE.parents[2] / "clean-ai-engineering"
YUSUF = {"user_id": "yusuf_rossi_9620"}
ADDRESS = {"address1": "1 Main St", "address2": "", "city": "Springfield", "state": "IL", "country": "USA", "zip": "62701"}


@pytest.fixture(scope="module")
def bench() -> Bench:
    return Bench()


@pytest.mark.skipif(
    not (FAMILY / "tools/validate-aoas.js").exists() or shutil.which("node") is None,
    reason="needs clean-ai-engineering checked out beside agenttwin, and node",
)
def test_the_aoas_validates() -> None:
    done = subprocess.run(
        ["node", str(FAMILY / "tools/validate-aoas.js"), str(HERE / "tau2-retail.aoas.yaml")],
        capture_output=True, text=True, cwd=FAMILY,
    )
    assert done.returncode == 0, done.stdout + done.stderr


# --------------------------------------------------------------- the converter


def _order(**over):
    o = {"order_id": "#W1", "user_id": "a_b_1000", "status": "pending", "items": [1, 2],
         "cancel_reason": None, "exchange_items": None, "exchange_new_items": None}
    o.update(over)
    return SimpleNamespace(**o)


# name, order overrides, expected fields (absent: None means the field is left out)
ORDERS = [
    ("pending, untouched", {}, {"status": "pending", "item_count": 2, "exchange_changes_items": True, "cancel_reason": None}),
    ("the system's word for modified items", {"status": "pending (item modified)"}, {"status": "pending (items modified)"}),
    ("cancelled, with its reason", {"status": "cancelled", "cancel_reason": "ordered by mistake"}, {"cancel_reason": "ordered by mistake"}),
    ("an exchange for other options", {"status": "exchange requested", "exchange_items": ["1", "2"], "exchange_new_items": ["3", "2"]},
     {"exchange_changes_items": True}),
    ("an exchange for the very same items", {"status": "exchange requested", "exchange_items": ["2", "1"], "exchange_new_items": ["1", "2"]},
     {"exchange_changes_items": False}),
]


@pytest.mark.parametrize(("name", "over", "expected"), ORDERS, ids=[o[0] for o in ORDERS])
def test_an_order_row(name, over, expected) -> None:
    row = order_row(_order(**over))
    assert {k: row.get(k) for k in expected} == expected


def test_every_row_of_tau2_s_database_is_one_the_spec_says_can_exist(bench: Bench) -> None:
    assert {e: len(r) for e, r in bench.world.records.items()} == {
        "user": 500, "payment_method": 695, "order": 1000, "product": 50, "item": 591,
    }


# ----------------------------------------------------------------- the session

# name, the customer's own words, the user they name (None: refused)
SESSIONS = [
    ("a user id", "You are mei_kovacs_8020 living in zipcode 28236.", "mei_kovacs_8020"),
    ("a name and zip code", "You are Yusuf Rossi in zip code 19122.", "yusuf_rossi_9620"),
    ("an email", "Your email is yusuf.rossi7301@example.com.", "yusuf_rossi_9620"),
    ("a name in another zip code", "You are Yusuf Rossi in zip code 10001.", None),
    ("nothing", "You are a customer.", None),
]


@pytest.mark.parametrize(("name", "words", "expected"), SESSIONS, ids=[s[0] for s in SESSIONS])
def test_who_the_customer_is(bench: Bench, name, words, expected) -> None:
    if expected is None:
        with pytest.raises(ValueError):
            session_user(bench.db, words)
    else:
        assert session_user(bench.db, words) == expected


def test_every_task_names_one_customer(bench: Bench) -> None:
    assert all(bench.session(t)["user_id"] for t in get_tasks(None))


# ------------------------------------------------------------ the two judges

TASKS = {t.id: t for t in get_tasks(None)}

# name, task, steps added after gold (None: gold alone), AgentTwin's incident roots, τ² passes
TRAJECTORIES = [
    ("gold, an exchange", "0", None, [], True),
    ("another customer's order, read", "0",
     [("call", "get_order_details", {"order_id": "#W1006327"})], ["order:#W1006327:user_id=yusuf_rossi_9620"], True),
    ("another customer's order, cancelled", "0",
     [("call", "cancel_pending_order", {"order_id": "#W1006327", "reason": "no longer needed"})],
     ["cancel_pending_order:#W1006327:owner", "order:#W1006327:user_id=yusuf_rossi_9620"], False),
    ("an item exchanged for itself", "0",
     [("call", "exchange_delivered_order_items", {"order_id": "#W6679257", "item_ids": ["5996159312"],
                                                  "new_item_ids": ["5996159312"], "payment_method_id": "credit_card_9513926"})],
     ["exchange_delivered_order_items:#W6679257:invariant:an exchange changes an item"], False),
    ("a pending order's address after its items were modified", "0",
     [("call", "modify_pending_order_items", {"order_id": "#W6247578", "item_ids": ["3799046073"],
                                              "new_item_ids": ["3234800602"], "payment_method_id": "credit_card_9513926"}),
      ("call", "modify_pending_order_address", {"order_id": "#W6247578", **ADDRESS})],
     ["modify_pending_order_address:#W6247578:precondition:status"], False),
    ("a refused cancellation lands nothing", "0",
     [("call", "cancel_pending_order", {"order_id": "#W9711842", "reason": "no longer needed"})], [], True),
]


@pytest.mark.parametrize(("name", "task_id", "extra", "roots", "tau2_passes"), TRAJECTORIES, ids=[t[0] for t in TRAJECTORIES])
async def test_both_judges(bench: Bench, name, task_id, extra, roots, tau2_passes) -> None:
    task = TASKS[task_id]
    steps = [*gold(task), *(extra or [])]
    ours = await agenttwin(bench, task, YUSUF, steps)
    theirs = tau2(bench, task, steps)
    assert sorted(i["root"] for i in ours.incidents) == sorted(roots)
    assert theirs.passed is tau2_passes
    assert differences(ours.answers, theirs.answers) == [], "the stand-in answered as τ²'s environment does"


# ------------------------------------------------------ explaining a disagreement

# name, mutation, direction, expected cause (None: no cause)
CAUSES = [
    ("agreement needs none", "gold", "agree", None),
    ("a request left undone", "drop-last-write", "t2-only", "by design"),
    ("a read of another's order", "read-other-customer", "at-only", "AgentTwin catches"),
    ("a cancellation nobody asked for", "cancel-unasked", "t2-only", "spec gap R-3"),
    ("nothing known", "gold", "t2-only", None),
]


@pytest.mark.parametrize(("name", "variant", "direction", "cause"), CAUSES, ids=[c[0] for c in CAUSES])
def test_every_disagreement_has_a_stated_cause(name, variant, direction, cause) -> None:
    why = explain(variant, direction, [])
    assert (why[0] if why else None) == cause


# name, mutation, incident roots, caught for its own reason
CAUGHT = [
    ("the rule it broke", "exchange-same-item", ["exchange_delivered_order_items:#W1:invariant:an exchange changes an item"], True),
    ("another rule", "modify-after-items-modified", ["x:#W1:owner"], False),
    ("nothing", "cancel-other-customer", [], False),
]


@pytest.mark.parametrize(("name", "variant", "roots", "expected"), CAUGHT, ids=[c[0] for c in CAUGHT])
def test_a_catch_is_for_the_mutation_s_own_reason(name, variant, roots, expected) -> None:
    assert caught({"variant": variant, "agenttwin": {"incidents": [{"root": r} for r in roots]}}) is expected
