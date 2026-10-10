"""A scenario's own calendar (0.9.0): what loads, what is refused, and what it does.

Table-driven, on the lending-library fixture. Each row is a calendar entry a
scenario carries and either `None` (it loads) or the message it is refused with.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agenttwin import Live, fire, load
from agenttwin.scenario_file import InvalidScenario, load_scenario
from tests.test_loader import SPEC, WORLD, write

LIES = {
    "id": "scanner-marks-it-returned",
    "entity": "loan",
    "where": [{"field": "id", "equals": ["L-1"]}],
    "sets": {"status": "returned", "days_overdue": 0},
    "record_only": True,
    "by": "returns scanner",
}


def scenario(tmp_path: Path, calendar: list[dict], world: dict | None = None) -> Path:
    write(tmp_path, SPEC, world or WORLD)
    path = tmp_path / "s.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "awd-scenario/v0",
                "scenario": "a lying record",
                "world": "branch.world.yaml",
                "as": "M-1",
                "actor": {"says": ["where is L-1"]},
                "calendar": calendar,
                "expect": [{"truthful": "loan", "id": "L-1"}],
            }
        )
    )
    return path


# [name, calendar entries, error regex or None]
LOADS = [
    ("a record_only entry at t0 loads", [LIES], None),
    ("at may say t0 explicitly", [{**LIES, "at": "0h"}], None),
    ("a real change at t0 loads", [{**LIES, "record_only": False}], None),
    ("no calendar loads", [], None),
    ("a later moment is the world's, not the scenario's", [{**LIES, "at": "10h"}], "fires at t0"),
    ("an entity no system owns", [{**LIES, "entity": "fine_print"}], "no system owns"),
    ("a field the entity lacks", [{**LIES, "sets": {"colour": "red"}}], "does not have"),
    ("a value outside the enum", [{**LIES, "sets": {"status": "lost"}}], "not one of"),
    ("one id twice", [LIES, LIES], "declared twice"),
    ("an unknown key", [{**LIES, "when": "now"}], "when"),
]


@pytest.mark.parametrize("name,calendar,error", LOADS, ids=[r[0] for r in LOADS])
def test_a_scenario_calendar_loads_or_says_why(tmp_path, name, calendar, error) -> None:
    path = scenario(tmp_path, calendar)
    if error is None:
        loaded = load_scenario(path)
        assert len(loaded.events()) == len(calendar)
        assert all(e.at_s == 0 for e in loaded.events())
        return
    with pytest.raises(InvalidScenario, match=error):
        load_scenario(path)


def test_an_id_the_world_already_uses_is_refused(tmp_path) -> None:
    world = {**WORLD, "calendar": [{**LIES, "at": "10h"}]}
    with pytest.raises(InvalidScenario, match="already the world's"):
        load_scenario(scenario(tmp_path, [LIES], world))


# [name, record_only, record status, truth status, cause]
FIRED = [
    ("record_only: the record lies, the truth stays", True, "returned", "open", LIES["id"]),
    ("a real change moves both", False, "returned", "returned", None),
]


@pytest.mark.parametrize("name,record_only,record,truth,cause", FIRED, ids=[r[0] for r in FIRED])
def test_firing_a_scenario_entry(tmp_path, name, record_only, record, truth, cause) -> None:
    path = scenario(tmp_path, [{**LIES, "record_only": record_only}])
    loaded = load_scenario(path)
    live = Live.start(load(tmp_path / "branch.world.yaml"))
    (event,) = loaded.events()
    assert fire(live, event) == ("L-1",)
    assert live.get("loan", "L-1")["status"] == record
    assert live.truth()["loan"]["L-1"]["status"] == truth
    assert live.cause("loan", "L-1", "status") == cause
