"""`python -m agenttwin scaffold` — a new agent's AgentTwin, from its AOAS alone.

On the lending library, so the scaffold is tested on a domain it was not
written for. What must hold: everything it writes loads the way the runner
will load it; the records sit on the edges the conditions declare; and the
scenarios it writes run against an implementation through a binding.
"""

from __future__ import annotations

import copy
import py_compile
from pathlib import Path

import pytest
import yaml

from agenttwin import load, load_scenario, run_one
from agenttwin.scaffold import scaffold
from tests.test_loader import SPEC, WORLD, write
from tests.test_runner import toy


def library_aoas(tmp_path: Path) -> Path:
    write(tmp_path, SPEC, WORLD)
    return tmp_path / "library.aoas.yaml"


@pytest.fixture
def scaffolded(tmp_path: Path) -> Path:
    out = tmp_path / "repo"
    scaffold(library_aoas(tmp_path), out)
    return out


def test_everything_it_writes_loads(scaffolded: Path) -> None:
    world = load(scaffolded / "worlds" / "library-desk.yaml")
    assert world.records["member"], "the customer and the stranger"
    scenarios = sorted((scaffolded / "scenarios").glob("*.yaml"))
    assert scenarios
    for path in scenarios:
        load_scenario(path)
    py_compile.compile(str(scaffolded / "evals" / "agenttwin_binding.py"), doraise=True)
    assert yaml.safe_load((scaffolded / "gates.yaml").read_text())["agent"] == "library-desk"


# [why the row must exist, field values it must carry]
EDGES = [
    ("renew exactly on its overdue limit", {"days_overdue": 7, "status": "open"}),
    ("renew one day past it", {"days_overdue": 8, "status": "open"}),
    ("renew refused in another state", {"status": "renewed"}),
    ("a loan that belongs to somebody else", {"member_id": "ME-1002"}),
]


@pytest.mark.parametrize("name,values", EDGES, ids=[e[0] for e in EDGES])
def test_the_records_sit_on_the_declared_edges(scaffolded: Path, name, values) -> None:
    loans = yaml.safe_load((scaffolded / "worlds" / "library-desk.yaml").read_text())["records"][
        "loan"
    ]
    assert any(all(row.get(k) == v for k, v in values.items()) for row in loans), loans


def test_a_row_the_invariants_forbid_is_dropped_and_said(tmp_path: Path) -> None:
    """Renewal of overdue loans only: the refused-state variant of its happy row
    is *renewed and one day overdue*, which "only an open loan is overdue"
    forbids — so it is dropped, and the world file says so."""
    spec = copy.deepcopy(SPEC)
    spec["operations"]["renew"]["preconditions"].append({"field": "days_overdue", "at_least": 1})
    write(tmp_path, spec, WORLD)
    out = tmp_path / "repo"
    scaffold(tmp_path / "library.aoas.yaml", out)
    text = (out / "worlds" / "library-desk.yaml").read_text()
    assert "dropped as impossible" in text
    assert "only an open loan is overdue" in text
    load(out / "worlds" / "library-desk.yaml")


# [scenario file, scenario it must be]
WRITTEN = [
    ("renew-lands.yaml", "renew lands when every condition holds"),
    ("get_loan-answers.yaml", "get loan answers from the record"),
    ("a-stranger-learns-nothing.yaml", "another customer's record is neither shown nor touched"),
    ("planted-in-loan-due_note.yaml", None),
]


@pytest.mark.parametrize("file,title", WRITTEN, ids=[w[0] for w in WRITTEN])
def test_the_scenarios_it_writes(scaffolded: Path, file, title) -> None:
    path = scaffolded / "scenarios" / file
    if title is None:
        assert not path.exists(), "due_note is not declared untrusted, so nothing is planted"
    else:
        assert load_scenario(path).scenario == title


async def test_the_scenarios_run_through_a_binding(scaffolded: Path) -> None:
    """Drafts, not verdicts — but every one must reach the agent and come back
    with an answer about it, not a broken file or a broken run."""
    for path in sorted((scaffolded / "scenarios").glob("*.yaml")):
        result = await run_one(path, toy())
        assert result.status in ("passed", "failed"), (path.name, result.error, result.cases)


def test_it_does_not_overwrite_without_force(tmp_path: Path) -> None:
    aoas, out = library_aoas(tmp_path), tmp_path / "repo"
    assert scaffold(aoas, out)
    (out / "gates.yaml").write_text("mine\n")
    assert scaffold(aoas, out) == []
    assert (out / "gates.yaml").read_text() == "mine\n"
    assert scaffold(aoas, out, force=True)
