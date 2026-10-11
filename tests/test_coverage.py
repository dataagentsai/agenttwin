"""Which scenarios to run, and which are missing (T-109) — `agenttwin.coverage`.

Table-driven, on the lending library from `test_loader` first, so the walker,
the feasibility rule and the reading of a scenario are shown on a domain they
were not written for; then on the clothing and motor-claims AOAS and their own
suites, read-only, where they are checked out alongside.

What must hold: the walker walks every transition; no infeasible pair is
counted; a scenario is read for the intent, state, persona, perturbation and
transitions it actually exercises; and the skeletons written for the gaps load
and close them — every feasible pair and every transition, once added.
"""

from __future__ import annotations

import ast
import copy
import os
from pathlib import Path

import pytest
import yaml

from agenttwin.__main__ import main
from agenttwin.coverage import (
    NO_ROW,
    Transition,
    choose,
    observe,
    report,
    scenario_files,
    skeletons,
    space,
    walk,
    write_skeletons,
)
from agenttwin.pairwise import allpairs
from agenttwin.spec import load_spec
from tests.test_loader import SPEC, WORLD, write

ROOT = Path(__file__).resolve().parents[1]
# The family's repositories sit beside this one; AGENTTWIN_FAMILY says where
# else, for a checkout (a worktree) that is not beside them.
FAMILY = Path(os.environ.get("AGENTTWIN_FAMILY", ROOT.parent))
EXAMPLES = FAMILY / "clean-ai-engineering" / "drafts" / "examples"

INTENTS = {
    "loan_status": {"answered_by": "direct", "via": ["get_loan"], "examples": ["Is my loan due?"]},
    "renew": {"answered_by": "loop", "via": ["renew"], "examples": ["Renew my book please"]},
    "fine_waiver": {"answered_by": "refusal", "refusal": "R-FINES", "examples": ["Waive my fine"]},
}


def library(tmp_path: Path, *, intents: bool = True) -> tuple[Path, Path]:
    spec, world = copy.deepcopy(SPEC), copy.deepcopy(WORLD)
    if intents:
        spec["intents"] = INTENTS
    world["records"]["loan"] += [
        {"id": "L-2", "member_id": "M-1", "status": "renewed", "days_overdue": 0},
    ]
    path = write(tmp_path, spec, world)
    return tmp_path / "library.aoas.yaml", path


def t(src: str, dst: str, by: str = "external") -> Transition:
    return Transition("m", "e", "status", src, dst, by)


# ------------------------------------------------------------------ the walker

# [name, transitions, start, paths as "a>b" hops]
WALKS = [
    ("a chain is one path", [t("a", "b"), t("b", "c")], "a", [["a>b", "b>c"]]),
    (
        "a fork needs a second path from the start",
        [t("a", "b"), t("a", "c")],
        "a",
        [["a>b"], ["a>c"]],
    ),
    (
        "a cycle is walked round, not restarted",
        [t("a", "b"), t("b", "a"), t("a", "c")],
        "a",
        [["a>b", "b>a", "a>c"]],
    ),
    (
        "the shortest way to an edge not yet walked",
        [t("a", "b"), t("b", "c"), t("a", "d"), t("d", "e")],
        "a",
        [["a>b", "b>c"], ["a>d", "d>e"]],
    ),
    (
        "an edge the start cannot reach starts from its own source",
        [t("a", "b"), t("x", "y")],
        "a",
        [["a>b"], ["x>y"]],
    ),
    ("no transitions, no paths", [], "a", []),
]


@pytest.mark.parametrize(("name", "edges", "start", "paths"), WALKS, ids=[w[0] for w in WALKS])
def test_the_walker_walks_every_transition(name, edges, start, paths) -> None:
    walked = walk(edges, start)
    assert [[f"{h.src}>{h.dst}" for h in p] for p in walked] == paths
    assert {h for p in walked for h in p} == set(edges)


# ------------------------------------------------------------------ the space

# [name, combination, may it exist]
FEASIBLE = [
    ("an intent on a row, in a machine state", {"intent": "renew", "state": "open"}, True),
    ("an intent on a row, naming none", {"intent": "renew", "state": NO_ROW}, True),
    ("a refusal has no row to be in a state", {"intent": "fine_waiver", "state": "open"}, False),
    ("a refusal, with no row", {"intent": "fine_waiver", "state": NO_ROW}, True),
    ("a fault on a tool needs a tool", {"intent": "fine_waiver", "perturbation": "slow"}, False),
    (
        "the model can fail anywhere",
        {"intent": "fine_waiver", "perturbation": "provider_throttled"},
        True,
    ),
    ("a stale read needs a row", {"state": NO_ROW, "perturbation": "stale_read"}, False),
    (
        "a stale read on a row",
        {"intent": "loan_status", "state": "open", "perturbation": "stale_read"},
        True,
    ),
    ("any persona meets any fault", {"persona": "impatient", "perturbation": "lost_reply"}, True),
]


@pytest.mark.parametrize(("name", "combo", "ok"), FEASIBLE, ids=[f[0] for f in FEASIBLE])
def test_only_what_can_exist_is_counted(tmp_path: Path, name, combo, ok) -> None:
    aoas, _ = library(tmp_path)
    assert space(load_spec(aoas)).allowed(combo) is ok


# [name, intents declared, dimension sizes, transitions as names]
SPACES = [
    (
        "intents declared",
        True,
        {"intent": 3, "state": 4, "persona": 6, "perturbation": 9},
        [
            "loan_status: open -> renewed by renew",
            "loan_status: open -> returned by external",
            "loan_status: renewed -> returned by external",
        ],
    ),
    (
        "none declared: one per offered operation",
        False,
        {"intent": 4, "state": 4, "persona": 6, "perturbation": 9},
        None,
    ),
]


@pytest.mark.parametrize(
    ("name", "intents", "sizes", "transitions"), SPACES, ids=[s[0] for s in SPACES]
)
def test_the_dimensions_come_from_the_aoas(tmp_path: Path, name, intents, sizes, transitions):
    aoas, _ = library(tmp_path, intents=intents)
    sp = space(load_spec(aoas))
    assert {k: len(v) for k, v in sp.dimensions.items()} == sizes
    if transitions is not None:
        assert [x.name for x in sp.transitions] == transitions


# ------------------------------------------------------- reading a scenario


def scenario(**extra) -> dict:
    return {
        "apiVersion": "awd-scenario/v0",
        "scenario": "s",
        "world": "../branch.world.yaml",
        "as": "M-1",
        "max_turns": 1,
        "actor": {"says": ["hello"]},
        "expect": [{"world": "unchanged"}],
        **extra,
    }


# [name, scenario fields, intents, states, persona, perturbations, transitions]
READS = [
    (
        "an intent it declares",
        {"discharges": ["intent:loan_status"]},
        {"loan_status"},
        {},
        "plain",
        ("none",),
        set(),
    ),
    (
        "an intent's example, said",
        {"actor": {"says": ["Renew my book, please!"]}},
        {"renew"},
        {},
        "plain",
        ("none",),
        set(),
    ),
    (
        "a refusal it discharges",
        {"discharges": ["R-FINES"]},
        {"fine_waiver"},
        {},
        "plain",
        ("none",),
        set(),
    ),
    (
        "a write it calls, on an open loan, that lands",
        {
            "actor": {"says": ["L-1 please"]},
            "model": [{"calls": [{"renew": {"id": "L-1"}}]}],
            "expect": [{"effect": "renew", "times": 1}],
        },
        {"renew"},
        {"L-1": ("loan", "open")},
        "plain",
        ("none",),
        {"loan_status: open -> renewed by renew"},
    ),
    (
        "a write checked never to land walks nothing",
        {
            "model": [{"calls": [{"renew": {"id": "L-1"}}]}],
            "expect": [{"effect": "renew", "times": 0}],
        },
        {"renew"},
        {"L-1": ("loan", "open")},
        "plain",
        ("none",),
        set(),
    ),
    (
        "a write its preconditions refuse walks nothing",
        {"model": [{"calls": [{"renew": {"id": "L-2"}}]}]},
        {"renew"},
        {"L-2": ("loan", "renewed")},
        "plain",
        ("none",),
        set(),
    ),
    (
        "a never-called check is not evidence of the intent",
        {"expect": [{"called": "renew", "times": 0}]},
        set(),
        {},
        "plain",
        ("none",),
        set(),
    ),
    (
        "a stale read moves the row, by the outside world",
        {
            "discharges": ["intent:loan_status"],
            "perturbations": [
                {
                    "kind": "stale_read",
                    "tool": "get_loan",
                    "entity": "loan",
                    "key": "L-1",
                    "sets": {"status": "returned"},
                },
            ],
        },
        {"loan_status"},
        {"L-1": ("loan", "open")},
        "plain",
        ("stale_read",),
        {"loan_status: open -> returned by external"},
    ),
    (
        "a calendar entry moves it before the first turn",
        {
            "discharges": ["intent:loan_status"],
            "calendar": [
                {
                    "id": "c",
                    "entity": "loan",
                    "where": [{"field": "id", "equals": ["L-2"]}],
                    "sets": {"status": "returned"},
                },
            ],
        },
        {"loan_status"},
        {"L-2": ("loan", "returned")},
        "plain",
        ("none",),
        {"loan_status: renewed -> returned by external"},
    ),
    (
        "a record that lies moves nothing",
        {
            "discharges": ["intent:loan_status"],
            "calendar": [
                {
                    "id": "c",
                    "entity": "loan",
                    "where": [{"field": "id", "equals": ["L-2"]}],
                    "sets": {"status": "returned"},
                    "record_only": True,
                },
            ],
        },
        {"loan_status"},
        {"L-2": ("loan", "renewed")},
        "plain",
        ("none",),
        set(),
    ),
    (
        "a model-played customer is their persona",
        {
            "discharges": ["intent:renew"],
            "actor": {"kind": "model", "persona": "impatient", "situation": "renew L-1"},
            "perturbations": [{"kind": "provider_throttled"}],
        },
        {"renew"},
        {"L-1": ("loan", "open")},
        "impatient",
        ("provider_throttled",),
        set(),
    ),
]


@pytest.mark.parametrize(
    ("name", "fields", "intents", "states", "persona", "faults", "walked"),
    READS,
    ids=[r[0] for r in READS],
)
def test_a_scenario_is_read_for_what_it_exercises(
    tmp_path: Path, name, fields, intents, states, persona, faults, walked
) -> None:
    aoas, _ = library(tmp_path)
    (tmp_path / "scenarios").mkdir()
    path = tmp_path / "scenarios" / "s.yaml"
    path.write_text(yaml.safe_dump(scenario(**fields)))
    seen = observe(path, space(load_spec(aoas)))
    assert not seen.skipped
    assert set(seen.intents) == intents
    assert seen.states == states
    assert seen.persona == persona
    assert seen.perturbations == faults
    assert {x.name for x in seen.transitions} == walked


# ---------------------------------------------------- choosing, and closing


def suites() -> list[tuple[str, Path, Path | None, Path | None]]:
    """[name, AOAS, world, scenarios] — the family's agents, read-only."""
    rows = [
        (
            "clothing",
            EXAMPLES / "support-agent.aoas.yaml",
            FAMILY / "reference-agent" / "worlds" / "clothing.yaml",
            FAMILY / "reference-agent" / "scenarios",
        ),
        (
            "motor claims",
            EXAMPLES / "motor-claims-fnol.aoas.yaml",
            FAMILY / "clean-ai-engineering/gates/motor-claims-fnol/worlds/motor-claims-fnol.yaml",
            FAMILY / "clean-ai-engineering/gates/motor-claims-fnol/scenarios",
        ),
    ]
    return [r for r in rows if r[1].exists() and r[2].exists() and r[3].exists()]


@pytest.mark.parametrize("aoas", [s[1] for s in suites()], ids=[s[0] for s in suites()])
def test_the_chosen_rows_cover_every_feasible_pair_and_nothing_infeasible(aoas: Path) -> None:
    sp = space(load_spec(aoas))
    rows, _ = choose(sp, allpairs)
    assert all(sp.allowed(r) for r in rows)
    from agenttwin.coverage import _pairs_of

    covered = set().union(*(_pairs_of(r) for r in rows))
    assert sp.pairs() <= covered


def test_on_the_library_the_skeletons_close_every_gap(tmp_path: Path) -> None:
    aoas, world = library(tmp_path)
    out = tmp_path / "scenarios"
    rep = report(aoas, [])
    written = write_skeletons(skeletons(rep, world, out, allpairs), out)
    assert written
    after = report(aoas, scenario_files([out]))
    assert len(after.covered_pairs) == len(after.pairs)
    assert len(after.covered_transitions) == len(after.space.transitions) == 3


@pytest.mark.parametrize(
    ("name", "aoas", "world", "existing"), suites(), ids=[s[0] for s in suites()]
)
def test_on_the_family_the_skeletons_close_every_gap(
    tmp_path: Path, name, aoas: Path, world: Path, existing: Path
) -> None:
    before = report(aoas, scenario_files([existing]))
    assert before.missing_pairs and before.missing_transitions, "a gap to close"
    out = tmp_path / "scenarios"
    write_skeletons(skeletons(before, world, out, allpairs), out)
    after = report(aoas, scenario_files([existing, out]))
    assert after.missing_pairs == set()
    assert after.missing_transitions == []


# ------------------------------------------------------------------ the CLI


def test_coverage_and_pairwise_from_the_command_line(tmp_path: Path, capsys) -> None:
    aoas, world = library(tmp_path)
    repo = tmp_path / "repo"
    assert (
        main(["scaffold", str(aoas), "--out", str(repo), "--pairwise", "--world", str(world)]) == 0
    )
    assert "skeleton(s)" in capsys.readouterr().out
    summary = tmp_path / "coverage.json"
    assert main(["coverage", str(aoas), str(repo / "scenarios"), "--json", str(summary)]) == 0
    printed = capsys.readouterr().out
    assert "transitions  3/3" in printed
    data = yaml.safe_load(summary.read_text())
    assert data["pairs"]["covered"] == data["pairs"]["total"]


def test_pairwise_without_a_world_says_so(tmp_path: Path, capsys) -> None:
    aoas, _ = library(tmp_path)
    assert main(["scaffold", str(aoas), "--out", str(tmp_path / "x"), "--pairwise"]) == 2
    assert "no world" in capsys.readouterr().err


# ------------------------------------------------------- the one constraint

ADOPTED = {
    "allpairspy",
    "hypothesis_jsonschema",
    "hypothesis",
    "pyrit",
    "agentdojo",
    "scenario",  # LangWatch Scenario's import name
    "langwatch",
    "litellm",
}
ADAPTERS = {
    "pairwise.py",
    "promises.py",
    "attack_pyrit.py",
    "attack_agentdojo.py",
    "actor_langwatch.py",
}
ADAPTER_MODULES = {f"agenttwin.{a.removesuffix('.py')}" for a in ADAPTERS}


@pytest.mark.parametrize(
    "module",
    sorted(p.name for p in (ROOT / "agenttwin").glob("*.py") if p.name not in ADAPTERS),
)
def test_the_core_never_imports_an_adopted_tool(module: str) -> None:
    """ADOPTION.md, *The one constraint*: the adapters sit outside the core."""
    tree = ast.parse((ROOT / "agenttwin" / module).read_text())
    imported = {
        (n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    assert not imported & ADOPTED
    imported_modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert not ADAPTER_MODULES & imported_modules or module == "__main__.py"


@pytest.mark.parametrize(
    "adapter", ["attack_pyrit.py", "attack_agentdojo.py", "pairwise.py", "actor_langwatch.py"]
)
def test_an_adapter_imports_its_tool_only_where_it_is_called(adapter: str) -> None:
    """Importing an adapter must not import its tool: `agenttwin.attacks`
    resolves `pyrit` and `agentdojo` by name, and a missing extra should fail
    the scenario that asked for it, not the import of the package."""
    tree = ast.parse((ROOT / "agenttwin" / adapter).read_text())
    top_level = {
        (n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
        for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    assert not top_level & ADOPTED
