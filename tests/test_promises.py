"""The stand-in keeps its promises (T-109) — `agenttwin.promises`.

Property tests, generated from each tool's own `inputSchema` by
hypothesis-jsonschema: every answer validates against the declared
`outputSchema`; a refusal is a result, never an exception; and the row inside
an answer has the shape the AOAS declares.

Run over the lending library, and over the clothing and motor-claims worlds
read-only where they are checked out alongside. **What the projection breaks is
recorded below as a finding, not fixed by loosening a schema**: the table is
the list of known breaks, so a new one fails here and a fixed one says to strike
it off.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest

from agenttwin import Live, load
from agenttwin.promises import check_promises
from tests.test_loader import SPEC, WORLD, write

ROOT = Path(__file__).resolve().parents[1]
FAMILY = Path(os.environ.get("AGENTTWIN_FAMILY", ROOT.parent))


def library(tmp_path: Path) -> Path:
    spec, world = copy.deepcopy(SPEC), copy.deepcopy(WORLD)
    world["records"]["member"].append({"id": "M-2", "email": "n@example.org"})
    world["records"]["loan"] += [
        {"id": "L-2", "member_id": "M-1", "status": "renewed", "days_overdue": 0},
        {"id": "L-3", "member_id": "M-2", "status": "open", "days_overdue": 9},
    ]
    return write(tmp_path, spec, world)


# [name, world path (relative to the family, or None for the library), examples per
#  promise, known findings: {(tool, promise): text the finding's detail must carry}]
WORLDS = [
    ("the lending library", None, 15, {}),
    ("clothing", "reference-agent/worlds/clothing.yaml", 15, {}),
    ("clothing, a refund owed", "reference-agent/worlds/clothing-owed.yaml", 15, {}),
    (
        "motor claims",
        "clean-ai-engineering/gates/motor-claims-fnol/worlds/motor-claims-fnol.yaml",
        40,
        {
            # F-1 (2026-10-11). `register_claim` takes `incident_type` as any
            # string — its inputSchema says `string`, not the AOAS's enum — and
            # the stand-in stores it, so a claim of type '' exists; and the
            # claim it creates leaves every field `creates.sets` does not name
            # as null (`note`), where the AOAS types it text. Both surface when
            # the claim is listed.
            ("list_claims", "AOAS shape"): "incident_type: ",
        },
    ),
]


def world_for(tmp_path: Path, path: str | None) -> Path:
    if path is None:
        return library(tmp_path)
    found = FAMILY / path
    if not found.exists():
        pytest.skip(f"{path} is not checked out alongside")
    return found


@pytest.mark.parametrize(("name", "path", "examples", "known"), WORLDS, ids=[w[0] for w in WORLDS])
def test_the_projection_keeps_its_promises(tmp_path: Path, name, path, examples, known) -> None:
    findings = check_promises(
        load(world_for(tmp_path, path)), max_examples=examples, history=1, shrink=False
    )
    got = {(f.tool, f.promise): f.detail for f in findings}
    # Never the declared outputSchema, never an exception: those are the
    # contract itself, and no world may break them.
    assert not [f for f in findings if f.promise != "AOAS shape"], findings
    assert set(got) == set(known), findings
    for where, text in known.items():
        assert text in got[where]


# ------------------------------------------------------------ it has teeth


def off_the_enum(world):
    live = Live.start(world)
    live.rows["loan"]["L-1"]["status"] = "teleported"
    return live


def null_count(world):
    live = Live.start(world)
    live.rows["loan"]["L-1"]["days_overdue"] = None
    return live


# [name, live factory, project's unknown_record, the tool and promise that must be reported]
BROKEN = [
    ("a state the machine does not have", off_the_enum, "result", ("get_loan", "AOAS shape")),
    ("a count that is null", null_count, "result", ("get_loan", "AOAS shape")),
    (
        "an unknown row raised, not answered",
        Live.start,
        "raise",
        ("get_loan", "refusal is a result"),
    ),
]


@pytest.mark.parametrize(("name", "live", "mode", "expected"), BROKEN, ids=[b[0] for b in BROKEN])
def test_a_broken_promise_is_found(tmp_path: Path, name, live, mode, expected) -> None:
    findings = check_promises(
        load(library(tmp_path)),
        max_examples=15,
        history=0,
        live=live,
        unknown_record=mode,
        shrink=False,
    )
    assert expected in {(f.tool, f.promise) for f in findings}, findings
