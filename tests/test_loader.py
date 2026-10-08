"""The loader, on a world that is not the reference agent's.

Table-driven. The fixture is a lending library — deliberately not a shop — so
these tests also show the format carries no domain of its own: the same loader
composes a library and a clothing store, and knows neither.

Each row mutates the spec, the world, or both, and names the error it must
raise (or `None` for must-load). A row asserts the message, not merely that
something failed, so one check going quiet cannot hide behind another.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from agenttwin.loader import InvalidWorld, load
from agenttwin.spec import merge_patch

SCHEMA = json.loads((Path(__file__).parent.parent / "schema" / "awd.schema.json").read_text())

OWNED = {"field": "loan.member_id", "equals_session": "member_id"}

SPEC = {
    "apiVersion": "aoas/v0",
    "agent": {"id": "library-desk", "version": "1.0.0"},
    "entities": {
        "member": {"key": "id", "fields": {"id": {"type": "id"}, "email": {"type": "email"}}},
        "loan": {
            "key": "id",
            "fields": {
                "id": {"type": "id"},
                "member_id": {"type": "id", "ref": "member.id"},
                "status": {"type": "enum", "of": "loan_status"},
                "days_overdue": {"type": "int"},
                "due_note": {"type": "text"},
            },
            "invariants": [
                {
                    "name": "only an open loan is overdue",
                    "when": {"field": "days_overdue", "at_least": 1},
                    "then": {"field": "status", "equals": ["open"]},
                    "because": "a returned book cannot be late",
                }
            ],
        },
        "fine": {"key": "id", "fields": {"id": {"type": "id"}, "amount": {"type": "money"}}},
    },
    "state_machines": {
        "loan_status": {
            "states": ["open", "renewed", "returned"],
            "terminal": ["returned"],
            "transitions": [
                {"from": "open", "to": "renewed", "by": "renew"},
                {"from": ["open", "renewed"], "to": "returned", "by": "external"},
            ],
        }
    },
    "session": {"member_id": {"type": "id", "ref": "member.id"}},
    "operations": {
        "get_loan": {
            "entity": "loan",
            "side_effect": "read",
            "input": ["id"],
            "preconditions": [OWNED],
            "authority": "agent",
        },
        "renew": {
            "entity": "loan",
            "side_effect": "reversible",
            "input": ["id"],
            "preconditions": [
                OWNED,
                {"field": "status", "equals": ["open"]},
                {"field": "days_overdue", "at_most": 7},
            ],
            "effect": {"status": "renewed"},
            "identity": "id",
            "authority": "agent",
        },
        "note_due_date": {
            "entity": "loan",
            "side_effect": "reversible",
            "input": ["id", "note"],
            "effect": {"due_note": "$note"},
            "authority": "agent",
        },
        "waive_fine": {
            "entity": "fine",
            "side_effect": "irreversible",
            "input": ["id"],
            "identity": "id",
            "authority": "human_approval",
        },
    },
    "external": {
        "catalogue": {
            "owns": ["member", "loan"],
            "operations": ["get_loan", "renew", "note_due_date"],
        },
        "billing": {"owns": ["fine"], "operations": ["waive_fine"]},
    },
}

WORLD = {
    "apiVersion": "awd/v0",
    "name": "branch-library",
    "seed": 7,
    "spec": {"aoas": "library-desk", "version": "1.0.0", "path": "library.aoas.yaml"},
    "fidelity": {"faithful_about": ["loan status"], "not_faithful_about": ["fines"]},
    "systems": {
        "catalogue": {
            "projects": "catalogue",
            "presents": {
                "renew": {
                    "description": "Renew a loan.",
                    "refusal": "a {status} loan cannot be renewed",
                }
            },
        }
    },
    "records": {
        "member": [{"id": "M-1", "email": "m@example.org"}],
        "loan": [{"id": "L-1", "member_id": "M-1", "status": "open", "days_overdue": 2}],
    },
}


def write(tmp_path: Path, spec: dict, world: dict) -> Path:
    (tmp_path / "library.aoas.yaml").write_text(yaml.safe_dump(spec))
    path = tmp_path / "branch.world.yaml"
    path.write_text(yaml.safe_dump(world))
    return path


def world_(f):
    return lambda s, w: f(w)


def spec_(f):
    return lambda s, w: f(s)


def put(target: dict, key: str, value) -> None:
    target[key] = value


# [name, mutate(spec, world), error regex or None]
CASES = [
    ("the fixture loads", lambda s, w: None, None),
    # ---- the boundary: a world may not declare the domain
    ("a world declaring entities", world_(lambda w: put(w, "entities", {})), "entities"),
    (
        "a world declaring actions on a system",
        world_(lambda w: put(w["systems"]["catalogue"], "actions", {})),
        "actions",
    ),
    # ---- the citation
    (
        "citing another version",
        world_(lambda w: put(w["spec"], "version", "2.0.0")),
        "cites library-desk@2.0.0",
    ),
    (
        "citing another agent",
        world_(lambda w: put(w["spec"], "aoas", "front-desk")),
        "cites front-desk",
    ),
    (
        "a spec that is not there",
        world_(lambda w: put(w["spec"], "path", "missing.yaml")),
        "no agent specification",
    ),
    (
        "a spec that is not an agent spec",
        spec_(lambda s: put(s, "apiVersion", "other/v1")),
        "is not an agent specification",
    ),
    # ---- projection
    (
        "projecting a system the spec lacks",
        world_(lambda w: put(w["systems"]["catalogue"], "projects", "archive")),
        "does not declare",
    ),
    (
        "presenting an operation the system lacks",
        world_(lambda w: put(w["systems"]["catalogue"]["presents"], "waive_fine", {})),
        "does not expose",
    ),
    (
        "exposing an operation the spec lacks",
        spec_(lambda s: s["external"]["catalogue"]["operations"].append("reserve")),
        "does not define",
    ),
    (
        "an operation on an entity the system does not own",
        spec_(lambda s: s["external"]["catalogue"]["operations"].append("waive_fine")),
        "does not own",
    ),
    # ---- coherence, unchanged from before the split
    (
        "a seeded row outside its enum",
        world_(lambda w: put(w["records"]["loan"][0], "status", "lost")),
        "is not one of",
    ),
    (
        "a seeded row that cannot exist",
        world_(lambda w: put(w["records"]["loan"][0], "status", "returned")),
        "'only an open loan is overdue': a returned book",
    ),
    (
        "a dangling foreign key",
        world_(lambda w: put(w["records"]["loan"][0], "member_id", "M-9")),
        "matches no member",
    ),
    (
        "rows for an entity no system owns",
        world_(lambda w: put(w["records"], "fine", [{"id": "F-1"}])),
        "unknown entity 'fine'",
    ),
    (
        "an invariant over a missing field",
        spec_(lambda s: put(s["entities"]["loan"]["invariants"][0]["when"], "field", "late_by")),
        "late_by",
    ),
]


@pytest.mark.parametrize(("name", "mutate", "error"), CASES, ids=[c[0] for c in CASES])
def test_loading(tmp_path: Path, name: str, mutate, error: str | None) -> None:
    spec, world = copy.deepcopy(SPEC), copy.deepcopy(WORLD)
    mutate(spec, world)
    path = write(tmp_path, spec, world)
    if error is None:
        load(path)
        return
    with pytest.raises(InvalidWorld, match=error):
        load(path)


def test_composition(tmp_path: Path) -> None:
    """What comes from where — the table in loader.py, asserted."""
    world = load(write(tmp_path, SPEC, WORLD))

    assert set(world.entities) == {"member", "loan"}, "only what the projected systems own"
    assert world.entities["loan"].fields["status"].values == ("open", "renewed", "returned")
    renew = world.systems["catalogue"].actions["renew"]
    assert [c.field for c in renew.allowed_when] == ["status", "days_overdue"]
    assert renew.sets == {"status": "renewed"}
    assert renew.refusal == "a {status} loan cannot be renewed", "presentation is the world's"
    assert (
        world.systems["catalogue"].actions["get_loan"].refusal
        == "that is not possible in its current state"
    )


def test_an_effect_written_from_a_declared_input_is_enforced(tmp_path: Path) -> None:
    """`{due_note: $note}` with `note` among the operation's inputs. The stand-in
    takes the input and writes what it was given — a world that carried only the
    key could not, so the operation changed nothing."""
    action = load(write(tmp_path, SPEC, WORLD)).systems["catalogue"].actions["note_due_date"]
    assert [(i.name, i.type) for i in action.inputs] == [("note", "str")]
    assert action.sets_from_input == {"due_note": "note"}
    assert action.sets == {}, "never the literal '$note'"


def test_an_effect_from_an_input_nobody_declared_is_said_not_skipped(tmp_path: Path) -> None:
    """The statement no world can enforce: an effect naming an input the
    operation does not take. Reported, never dropped — silently skipping it would
    read, in every run, as a statement that held."""
    spec = copy.deepcopy(SPEC)
    spec["operations"]["note_due_date"]["effect"] = {"due_note": "$missing"}
    world = load(write(tmp_path, spec, WORLD))
    assert sorted((u.operation, u.reason) for u in world.unenforced) == [
        ("note_due_date", "'missing' is not one of this operation's declared inputs"),
    ]
    assert world.systems["catalogue"].actions["note_due_date"].sets == {}


def test_a_variant_is_its_base_plus_a_merge_patch(tmp_path: Path) -> None:
    (tmp_path / "base.aoas.yaml").write_text(yaml.safe_dump(SPEC))
    variant = {
        "apiVersion": "aoas/v0",
        "extends": {"id": "library-desk", "version": "1.0.0", "path": "base.aoas.yaml"},
        "agent": {"id": "strict-desk"},
        "operations": {
            "renew": {
                "preconditions": [
                    OWNED,
                    {"field": "status", "equals": ["open"]},
                    {"field": "days_overdue", "at_most": 0},
                ]
            }
        },
    }
    (tmp_path / "library.aoas.yaml").write_text(yaml.safe_dump(variant))
    world = copy.deepcopy(WORLD)
    world["spec"]["aoas"] = "strict-desk"
    (tmp_path / "w.yaml").write_text(yaml.safe_dump(world))

    renew = load(tmp_path / "w.yaml").systems["catalogue"].actions["renew"]
    assert renew.allowed_when[-1].at_most == 0, "the patched precondition"
    assert renew.sets == {"status": "renewed"}, "the inherited effect"


def test_the_published_schema_accepts_the_fixture_and_refuses_the_domain() -> None:
    jsonschema.validate(WORLD, SCHEMA)
    for key in ("entities", "actions", "policies"):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**WORLD, key: {}}, SCHEMA)


# RFC 7386 Appendix A, row for row — the same table clean-ai-engineering's
# validator is held to, so the two implementations cannot drift apart.
MERGE_PATCH = [
    ({"a": "b"}, {"a": "c"}, {"a": "c"}),
    ({"a": "b"}, {"b": "c"}, {"a": "b", "b": "c"}),
    ({"a": "b"}, {"a": None}, {}),
    ({"a": "b", "b": "c"}, {"a": None}, {"b": "c"}),
    ({"a": ["b"]}, {"a": "c"}, {"a": "c"}),
    ({"a": "c"}, {"a": ["b"]}, {"a": ["b"]}),
    ({"a": {"b": "c"}}, {"a": {"b": "d", "c": None}}, {"a": {"b": "d"}}),
    ({"a": [{"b": "c"}]}, {"a": [1]}, {"a": [1]}),
    (["a", "b"], ["c", "d"], ["c", "d"]),
    ({"a": "b"}, ["c"], ["c"]),
    ({"a": "foo"}, None, None),
    ({"a": "foo"}, "bar", "bar"),
    ({"e": None}, {"a": 1}, {"e": None, "a": 1}),
    ([1, 2], {"a": "b", "c": None}, {"a": "b"}),
    ({}, {"a": {"bb": {"ccc": None}}}, {"a": {"bb": {}}}),
]


@pytest.mark.parametrize(
    ("target", "patch", "expected"),
    MERGE_PATCH,
    ids=[f"A.{i + 1}" for i in range(len(MERGE_PATCH))],
)
def test_rfc_7386(target, patch, expected) -> None:
    assert merge_patch(copy.deepcopy(target), patch) == expected


def test_ownership_is_enforced_not_reported(tmp_path: Path) -> None:
    """F-016's rule. A session comparison in the spec becomes a check the
    stand-in makes on every call, not an entry in the unenforced list."""
    actions = load(write(tmp_path, SPEC, WORLD)).systems["catalogue"].actions
    assert [(c.field, c.session) for c in actions["renew"].session_when] == [
        ("member_id", "member_id")
    ]
    assert [(c.field, c.session) for c in actions["get_loan"].session_when] == [
        ("member_id", "member_id")
    ]
    assert actions["note_due_date"].session_when == (), "the spec states no owner for it"


OWNERSHIP = [
    ("the owner", {"member_id": "M-1"}, True),
    ("a stranger", {"member_id": "M-2"}, False),
    ("no session at all", None, False),
    ("a session without the field", {"tenant": "T-1"}, False),
]


@pytest.mark.parametrize(("name", "session", "visible"), OWNERSHIP, ids=[o[0] for o in OWNERSHIP])
def test_a_row_is_visible_only_to_its_owner(
    tmp_path: Path, name: str, session, visible: bool
) -> None:
    renew = load(write(tmp_path, SPEC, WORLD)).systems["catalogue"].actions["renew"]
    row = {"id": "L-1", "member_id": "M-1", "status": "open", "days_overdue": 0}
    assert renew.visible_to(row, session) is visible


def test_every_field_property_the_schema_declares_is_read_by_the_loader() -> None:
    """The enumeration in `_entity` has now cost four fields.

    `advances`, `advances_when`, `untrusted` and `fresh_for` were each declared
    in a specification, dropped silently here, and found by something downstream
    behaving as though the declaration did not exist — a counter that never
    moved, a planted instruction in a field nobody marked, a read that never
    went stale. Every time the symptom was a world that quietly enforced less
    than it said.

    So the list stops being maintained by hand. This walks the AOAS schema's own
    field vocabulary and fails on any property the loader does not mention,
    which is the only version of this check that cannot itself go stale.
    """
    import json
    import re
    from pathlib import Path

    schema_path = (
        Path(__file__).resolve().parents[2] / "clean-ai-engineering" / "drafts" / "aoas.schema.json"
    )
    if not schema_path.exists():  # the sibling checkout is not always there
        pytest.skip("the AOAS schema is in a sibling checkout that is not present")

    # Declared, and deliberately not read — each with the reason, so the
    # exemption is a decision somebody made rather than a gap nobody noticed.
    carries_nothing = {
        "derived": (
            "prose: 'whole days since delivery'. It says how a value comes about, "
            "for a reader, and there is nothing machine-readable to act on. A world "
            "holds the value; what keeps a seeded one honest is the invariants."
        ),
    }
    declared = set(json.loads(schema_path.read_text())["$defs"]["field"]["properties"])
    source = (Path(__file__).resolve().parents[1] / "agenttwin" / "loader.py").read_text()
    body = source[source.index("def _entity(") :]
    unread = sorted(
        p for p in declared - set(carries_nothing) if not re.search(rf'"{p}"', body)
    )
    assert unread == [], (
        f"the schema declares these on a field and _entity never reads them: {unread} — "
        "a dropped property is a world that enforces less than its spec says. Read it, "
        "or add it to `carries_nothing` with why it cannot be acted on."
    )
    # And the other way: an exemption for something the loader has since started
    # reading is an exemption that has outlived its truth.
    stale = sorted(p for p in carries_nothing if re.search(rf'"{p}"', body))
    assert stale == [], f"exempted and read anyway: {stale}"


# [name, the note_due_date effect, reported as unenforced] — T-099: prose
# effects were dropped with nothing said.
EFFECTS = [
    ("a field set from an input is enforced", {"due_note": "$note"}, False),
    ("a prose effect is reported", "a reminder is sent to the member", True),
]


@pytest.mark.parametrize(("name", "effect", "reported"), EFFECTS, ids=[e[0] for e in EFFECTS])
def test_a_prose_effect_is_reported_never_dropped(tmp_path: Path, name: str, effect, reported: bool) -> None:
    spec = copy.deepcopy(SPEC)
    spec["operations"]["note_due_date"]["effect"] = effect
    world = load(write(tmp_path, spec, copy.deepcopy(WORLD)))
    prose = [u for u in world.unenforced if u.operation == "note_due_date" and "prose" in u.reason]
    assert bool(prose) is reported
