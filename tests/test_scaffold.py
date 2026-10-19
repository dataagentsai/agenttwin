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


# [name, scaffold prefix, row number, the field's pattern, key made] — T-099,
# T-122: the key is generated from the pattern itself, so any pattern the spec
# allows gets one. The first four are the keys the clothing, hotel and
# motor-claims specs have always had; they must not move.
KEYS = [
    ("no pattern: the scaffold's own prefix", "OR", 10001, None, "OR-10001"),
    ("motor claims: a literal prefix and fixed digits", "PO", 10001, "POL-[0-9]{6}", "POL-010001"),
    ("clothing: a character-class prefix keeps the scaffold's", "OR", 10001, "[A-Z]{1,3}-[0-9]{3,8}", "OR-10001"),
    ("motor claims: the stranger's row matches too", "CL", 19001, "CLM-[0-9]{6}", "CLM-019001"),
    ("tau2 retail: words and a number (T-122)", "US", 1001, "[a-z]+_[a-z]+_[0-9]{4}", "us_us_1001"),
    ("tau2: alternation takes the first branch", "PM", 10001, "(credit_card|paypal|gift_card)_[0-9]{7}", "credit_card_0010001"),
    ("tau2: a non-letter literal before the digits", "OR", 10001, "#W[0-9]{7}", "#W0010001"),
    ("tau2: digits only", "PR", 19001, "[0-9]{10}", "0000019001"),
    ("a class that takes digits holds the number", "RE", 10001, "[A-Z0-9]{6}", "010001"),
    ("two digit runs count like an odometer", "FL", 10001, "HAT[0-9]{3}/2024-05-[0-9]{2}", "HAT100/2024-05-01"),
    ("no digits: the number in the class's own letters", "AI", 10001, "[A-Z]{3}", "OUR"),
    ("anchors take no characters", "CU", 1001, "^C[0-9]{4}$", "C1001"),
    ("a non-capturing alternation and \\d", "IN", 10001, r"(?:INV|BIL)-\d{5}", "INV-10001"),
    ("an unbounded run takes the whole number", "BI", 10001, "B[0-9a-f]+", "B10001"),
    ("too few digits wrap rather than stop", "PO", 10001, "POL-[0-9]{3}", "POL-001"),
]


@pytest.mark.parametrize(("name", "prefix", "n", "pattern", "key"), KEYS, ids=[k[0] for k in KEYS])
def test_a_key_is_shaped_like_the_field_pattern(name: str, prefix: str, n: int, pattern, key: str) -> None:
    import re

    from agenttwin.scaffold import _key

    assert _key(prefix, n, pattern) == key
    assert pattern is None or re.fullmatch(pattern, key)


# [name, pattern, row numbers] — distinct rows get distinct keys.
DISTINCT = [
    ("digits", "POL-[0-9]{6}", [10001, 10002, 19001]),
    ("words and digits", "[a-z]+_[a-z]+_[0-9]{4}", [1001, 1002]),
    ("letters only", "[A-Z]{3}", [10001, 10002, 19001]),
    ("two runs", "HAT[0-9]{3}/2024-05-[0-9]{2}", [10001, 10002, 19001]),
]


@pytest.mark.parametrize(("name", "pattern", "numbers"), DISTINCT, ids=[d[0] for d in DISTINCT])
def test_distinct_rows_get_distinct_keys(name: str, pattern: str, numbers: list[int]) -> None:
    from agenttwin.scaffold import _key

    assert len({_key("XX", n, pattern) for n in numbers}) == len(numbers)


# [name, pattern, what the one-line message says] — when no key can be made.
UNFILLABLE = [
    ("a lookahead", "A(?=B)B", "unsupported construct"),
    ("a back-reference", r"(a)\1", "unsupported construct"),
    ("a class no key character is in", "[^\x00-\x7f]", "accepts no character"),
    ("a word boundary inside the key", r"a\bb", "tried 'ab'"),
]


@pytest.mark.parametrize(("name", "pattern", "says"), UNFILLABLE, ids=[u[0] for u in UNFILLABLE])
def test_a_key_no_pattern_can_produce_stops_with_one_line(name: str, pattern: str, says: str) -> None:
    from agenttwin.scaffold import KeyPatternError, _key

    with pytest.raises(KeyPatternError, match="cannot make a key") as e:
        _key("PO", 10001, pattern)
    assert says in str(e.value) and "\n" not in str(e.value)


# [name, the member key's pattern, what stderr names] — through the CLI: one
# line naming the entity, the field and the pattern; exit 2, no traceback.
CLI_ERRORS = [
    ("unfillable", "M(?=X)X", "member.id: cannot make a key matching 'M(?=X)X'"),
    ("one key for two rows", "MEMBER", "member.id: pattern 'MEMBER' allows too few keys for 2 distinct rows"),
]


@pytest.mark.parametrize(("name", "pattern", "says"), CLI_ERRORS, ids=[c[0] for c in CLI_ERRORS])
def test_the_cli_names_the_entity_field_and_pattern(
    name: str, pattern: str, says: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agenttwin.__main__ import main

    spec = copy.deepcopy(SPEC)
    spec["entities"]["member"]["fields"]["id"]["pattern"] = pattern
    write(tmp_path, spec, WORLD)
    assert main(["scaffold", str(tmp_path / "library.aoas.yaml"), "--out", str(tmp_path / "repo")]) == 2
    err = capsys.readouterr().err.strip()
    assert says in err and "\n" not in err and "Traceback" not in err


def test_the_tau2_retail_example_scaffolds(tmp_path: Path) -> None:
    """T-122: it stopped on `[a-z]+_[a-z]+_[0-9]{4}`."""
    aoas = Path(__file__).parent.parent / "examples" / "tau2-retail" / "tau2-retail.aoas.yaml"
    out = tmp_path / "repo"
    assert scaffold(aoas, out)
    world = load(next((out / "worlds").glob("*.yaml")))
    assert [r["user_id"] for r in world.records["user"]] == ["us_us_1001", "us_us_1002"]
