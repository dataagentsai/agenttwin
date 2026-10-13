"""The τ²-bench telecom example: converter, session, stand-in and judges.

Needs τ²-bench importable, so it runs through `./run.sh -m pytest tests`; the
repository's own suite skips it. Table-driven throughout.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("tau2")

from tau2.domains.telecom.environment import get_tasks  # noqa: E402

from tau2_common.judge import agenttwin, differences, tau2  # noqa: E402
from tau2_common.mutate import gold  # noqa: E402
from tau2_telecom.judge import Bench, session_of  # noqa: E402
from tau2_telecom.offline import caught, explain  # noqa: E402

HERE = Path(__file__).resolve().parents[1]
FAMILY = HERE.parents[2] / "clean-ai-engineering"
JOHN = {"customer_id": "C1001", "phone_number": "555-123-2002"}
CONTRACT_ENDED = "[service_issue]airplane_mode_on|break_apn_settings|contract_end_suspension|unseat_sim_card[PERSONA:Easy]"
DATA = ("[mms_issue]airplane_mode_on|bad_network_preference|bad_wifi_calling|break_apn_mms_setting|"
        "break_app_both_permissions|data_mode_off|data_usage_exceeded|unseat_sim_card|"
        "user_abroad_roaming_disabled_off[PERSONA:Hard]")


@pytest.fixture(scope="module")
def bench() -> Bench:
    return Bench()


@pytest.mark.skipif(
    not (FAMILY / "tools/validate-aoas.js").exists() or shutil.which("node") is None,
    reason="needs clean-ai-engineering checked out beside agenttwin, and node",
)
def test_the_aoas_validates() -> None:
    done = subprocess.run(
        ["node", str(FAMILY / "tools/validate-aoas.js"), str(HERE / "tau2-telecom.aoas.yaml")],
        capture_output=True, text=True, cwd=FAMILY,
    )
    assert done.returncode == 0, done.stdout + done.stderr


# --------------------------------------------------------------- the converter

# name, line, expected derived fields
LINES = [
    ("the phone's line", "L1002", {"customer_id": "C1001", "contract_ended": False, "owner_overdue_bills": 0, "status": "Active"}),
    ("a suspended line", "L1003", {"customer_id": "C1001", "status": "Suspended"}),
    ("another customer's, who has an overdue bill", "L1004", {"customer_id": "C1002", "owner_overdue_bills": 1}),
]


@pytest.mark.parametrize(("name", "line", "expected"), LINES, ids=[x[0] for x in LINES])
def test_a_line_row(bench: Bench, name, line, expected) -> None:
    row = bench.rows["line"][line]
    assert {k: row.get(k) for k in expected} == expected


def test_every_row_of_tau2_s_database_is_one_the_spec_says_can_exist(bench: Bench) -> None:
    assert {e: len(r) for e, r in bench.world.records.items()} == {
        "customer": 4, "line": 9, "bill": 6, "plan": 5, "device": 9,
    }


# name, the customer's words, the session (None: refused)
SESSIONS = [
    ("a line's number", "You are John Smith with phone number 555-123-2002.", JOHN),
    ("a customer's contact number", "My number is 555-123-1002.", {"customer_id": "C1002", "phone_number": "555-123-1002"}),
    ("an unknown number", "My number is 555-000-0000.", None),
    ("no number", "You are John Smith.", None),
]


@pytest.mark.parametrize(("name", "words", "expected"), SESSIONS, ids=[s[0] for s in SESSIONS])
def test_who_the_customer_is(bench: Bench, name, words, expected) -> None:
    if expected is None:
        with pytest.raises(ValueError):
            session_of(bench.db, words)
    else:
        assert session_of(bench.db, words) == expected


# ------------------------------------------------------------ the two judges

TASKS = {t.id: t for t in get_tasks("base")}

# name, task, steps added after gold, AgentTwin's incident roots, τ² passes
TRAJECTORIES = [
    ("gold: the customer's fixes and the agent's refuel", DATA, [], [], True),
    ("gold: a transfer, the contract having ended", CONTRACT_ENDED, [], [], True),
    ("a resume after the contract ended", CONTRACT_ENDED,
     [("call", "resume_line", {"customer_id": "C1001", "line_id": "L1002"})],
     ["resume_line:L1002:precondition:contract_ended", "resume_line:L1002:precondition:owner_overdue_bills"], True),
    ("a refuel on another of the customer's lines", DATA,
     [("call", "refuel_data", {"customer_id": "C1001", "line_id": "L1001", "gb_amount": 1.0})], ["refuel_data:L1001:owner"], True),
    ("a payment request for a bill not overdue", DATA,
     [("call", "send_payment_request", {"customer_id": "C1001", "bill_id": "B1002"})],
     ["send_payment_request:B1002:precondition:status"], True),
    ("another customer's record, read", DATA,
     [("call", "get_customer_by_id", {"customer_id": "C1002"})],
     ["customer:C1002:customer_id=C1001,phone_number=555-123-2002"], True),
    ("a refused refuel lands nothing", DATA,
     [("call", "refuel_data", {"customer_id": "C1001", "line_id": "L1002", "gb_amount": 0.0})], [], True),
]


@pytest.mark.parametrize(("name", "task_id", "extra", "roots", "tau2_passes"), TRAJECTORIES, ids=[t[0] for t in TRAJECTORIES])
async def test_both_judges(bench: Bench, name, task_id, extra, roots, tau2_passes) -> None:
    task = TASKS[task_id]
    steps = [*gold(task), *extra]
    ours = await agenttwin(bench, task, JOHN, steps)
    theirs = tau2(bench, task, steps)
    assert sorted(i["root"] for i in ours.incidents) == sorted(roots)
    assert theirs.passed is tau2_passes
    assert differences(ours.answers, theirs.answers) == [], "the stand-in answered as τ²'s environment does"


async def test_dropping_the_customer_s_fix_fails_only_tau2(bench: Bench) -> None:
    """The fix is the customer's, on the phone, which AgentTwin's world does not hold (T-1)."""
    task = TASKS[DATA]
    steps = gold(task)
    last = max(i for i, s in enumerate(steps) if s[0] == "user_call")
    dropped = steps[:last] + steps[last + 1 :]
    assert (await agenttwin(bench, task, JOHN, dropped)).passed
    assert not tau2(bench, task, dropped).passed


# ------------------------------------------------------ explaining a disagreement

CAUSES = [
    ("agreement needs none", "gold", "agree", None),
    ("the customer's step undone", "drop-customer-fix", "t2-only", "dual control, outside the format (T-1)"),
    ("a write no assertion names", "refuel-other-line", "at-only", "τ² checks no records here (T-2)"),
    ("nothing known", "suspend-unasked", "t2-only", None),
]


@pytest.mark.parametrize(("name", "variant", "direction", "cause"), CAUSES, ids=[c[0] for c in CAUSES])
def test_every_disagreement_has_a_stated_cause(name, variant, direction, cause) -> None:
    why = explain(variant, direction, [])
    assert (why[0] if why else None) == cause


CAUGHT = [
    ("the rule it broke", "resume-contract-ended", ["resume_line:L1002:precondition:contract_ended"], True),
    ("another rule only", "refuel-over-cap", ["refuel_data:L1002"], False),
]


@pytest.mark.parametrize(("name", "variant", "roots", "expected"), CAUGHT, ids=[c[0] for c in CAUGHT])
def test_a_catch_is_for_the_mutation_s_own_reason(name, variant, roots, expected) -> None:
    assert caught({"variant": variant, "agenttwin": {"incidents": [{"root": r} for r in roots]}}) is expected
