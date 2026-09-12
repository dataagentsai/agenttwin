"""A world file and the agent spec it cites in, `World` out — with the
validation that makes a world trustworthy.

A world file that parses but declares a join to a non-existent entity, or seeds a
row that violates its own enum, is worse than one that fails to parse: it runs,
and every verdict it produces is quietly about a different world than the one
someone thought they wrote.

## What comes from where

| In the composed `World` | From |
|---|---|
| entities, fields, invariants | the agent spec — only those the projected systems **own** |
| actions, conditions, effects | the spec's operations, via `external.<system>.operations` |
| tool descriptions, refusal text | the world — how the stand-in system presents itself |
| records, seed, fidelity, resolution | the world |
| the scope each operation requires | the **binding**, passed to `project` — not the world |

The world file is **forbidden** from declaring entities or actions. Not
discouraged — the model rejects the keys. That is what "no domain rule appears
in both files" means when it is enforced rather than hoped for.

## What a world cannot enforce

A world twins systems, and a system can only check what it can see. A condition
over **another entity's** fields, and an effect that writes an **operation
input** the projection does not carry, are real statements of the agent spec that
no stand-in can evaluate. They are not dropped silently: each becomes an
`Unenforced` entry on the world, so a report can say which statements were never
tested against it. A comparison with the **session** is enforced — the caller
presents its session in the call's metadata (F-016).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agenttwin.spec import InvalidSpec, load_spec
from agenttwin.world import (
    Action,
    Condition,
    Entity,
    Fidelity,
    Field_,
    Input,
    Invariant,
    Resolution,
    SessionCondition,
    SpecRef,
    System,
    Unenforced,
    World,
)

API_VERSION = "awd/v0"


class InvalidWorld(Exception):
    """The file parsed and does not describe a coherent world."""


# ------------------------------------------------------------- the world file


class Presentation(BaseModel):
    """How a stand-in system presents one operation: the system's words, not the
    agent's. The spec says what may happen; this is what the system *says*."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str = ""
    refusal: str | None = None


class SystemFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    projects: str
    resolution: Resolution = "mock"
    presents: dict[str, Presentation] = Field(default_factory=dict)


class WorldFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    api_version: Literal["awd/v0"] = Field(alias="apiVersion")
    name: str
    version: int = 1
    seed: int = 0
    spec: SpecRef
    fidelity: Fidelity = Fidelity()
    systems: dict[str, SystemFile]
    records: dict[str, tuple[dict, ...]] = Field(default_factory=dict)


# ------------------------------------------------------------------- loading


def resolve_spec(world_path: Path | str) -> Path:
    """Where a world file's cited spec is, relative to the world file."""
    raw = yaml.safe_load(Path(world_path).read_text())
    return (Path(world_path).parent / raw["spec"]["path"]).resolve()


def load(path: Path | str, *, spec: Path | str | None = None) -> World:
    """Load a world and compose it with the spec it cites.

    `spec` overrides the world's own `spec.path` — for a world copied somewhere
    its relative path no longer reaches. The citation is still checked.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    try:
        wf = WorldFile.model_validate(raw)
    except ValidationError as e:
        raise InvalidWorld(f"{path}: {e}") from e

    spec_path = Path(spec) if spec is not None else path.parent / wf.spec.path
    try:
        doc = load_spec(spec_path)
    except InvalidSpec as e:
        raise InvalidWorld(str(e)) from e

    world = compose(wf, doc)
    _check(world)
    return world


def compose(wf: WorldFile, doc: dict) -> World:
    cited = (wf.spec.aoas, wf.spec.version)
    found = (doc["agent"]["id"], doc["agent"]["version"])
    if cited != found:
        raise InvalidWorld(
            f"world cites {cited[0]}@{cited[1]}; the spec found is {found[0]}@{found[1]}"
        )

    machines = doc.get("state_machines", {})
    external = doc.get("external", {})
    operations = doc.get("operations", {})
    unenforced: list[Unenforced] = []

    # Which entities this world holds: exactly those its systems own.
    owned: list[str] = []
    for sname, s in wf.systems.items():
        if s.projects not in external:
            raise InvalidWorld(
                f"system {sname!r} projects {s.projects!r}, which the spec does not declare"
            )
        for e in external[s.projects].get("owns", ()):
            if e not in owned:
                owned.append(e)

    entities: dict[str, Entity] = {}
    for en in doc.get("entities", {}):
        if en in owned:
            entities[en] = _entity(en, doc["entities"][en], machines)

    systems: dict[str, System] = {}
    for sname, s in wf.systems.items():
        exposed = external[s.projects].get("operations", ())
        for named, kind in ((s.presents, "presents"),):
            for op in named:
                if op not in exposed:
                    raise InvalidWorld(
                        f"system {sname!r} {kind} {op!r}, which {s.projects!r} does not expose"
                    )

        actions: dict[str, Action] = {}
        for op_name in exposed:
            if op_name not in operations:
                raise InvalidWorld(
                    f"{s.projects!r} exposes {op_name!r}, which the spec does not define"
                )
            op = operations[op_name]
            if op["entity"] not in entities:
                raise InvalidWorld(
                    f"{op_name!r} acts on {op['entity']!r}, which {s.projects!r} does not own"
                )

            def miss(statement: str, reason: str, op_name=op_name, sname=sname) -> None:
                unenforced.append(
                    Unenforced(system=sname, operation=op_name, statement=statement, reason=reason)
                )

            allowed, owner = _conditions(op.get("preconditions", ()), op["entity"], miss)
            required, _ = _conditions(op.get("owed_when", ()), op["entity"], miss)

            inputs = _inputs(op.get("input", ()), op["entity"], entities[op["entity"]])
            declared = {i.name for i in inputs}

            sets: dict[str, Any] = {}
            from_input: dict[str, str] = {}
            effect = op.get("effect")
            if isinstance(effect, dict):
                for field, value in effect.items():
                    if not (isinstance(value, str) and value.startswith("$")):
                        sets[field] = value
                    elif value[1:] in declared:
                        from_input[field] = value[1:]
                    else:
                        miss(
                            f"sets {field} from input {value}",
                            f"{value[1:]!r} is not one of this operation's declared inputs",
                        )

            shown = s.presents.get(op_name, Presentation())
            actions[op_name] = Action(
                entity=op["entity"],
                side_effect=op["side_effect"],
                allowed_when=allowed,
                session_when=owner,
                required_when=required,
                inputs=inputs,
                sets=sets,
                sets_from_input=from_input,
                description=shown.description,
                **({"refusal": shown.refusal} if shown.refusal is not None else {}),
            )
        systems[sname] = System(
            resolution=s.resolution,
            projects=s.projects,
            actions=actions,
        )

    return World(
        name=wf.name,
        version=wf.version,
        seed=wf.seed,
        spec=wf.spec,
        fidelity=wf.fidelity,
        entities=entities,
        systems=systems,
        records=wf.records,
        unenforced=tuple(unenforced),
    )


def _entity(name: str, spec: dict, machines: dict) -> Entity:
    fields = {}
    for fn, f in spec["fields"].items():
        values = f.get("values") or machines.get(f.get("of"), {}).get("states", ())
        when = f.get("advances_when")
        # Enumerated, and that has now cost three fields: `advances`,
        # `advances_when` and `untrusted` were each declared in a spec, dropped
        # silently here, and found by something downstream behaving as though the
        # declaration did not exist. Anything added to the field vocabulary has
        # to be added here too, and the schema is the list to check against.
        fields[fn] = Field_(
            type=f["type"],
            values=tuple(values),
            ref=f.get("ref"),
            untrusted=bool(f.get("untrusted", False)),
            pii=bool(f.get("pii", False)),
            advances=f.get("advances"),
            advances_when=Condition(**_local(when, name)) if when else None,
        )
    invariants = tuple(
        Invariant(
            name=i["name"],
            when=Condition(**_local(i["when"], name)),
            then=Condition(**_local(i["then"], name)),
            because=i.get("because", ""),
        )
        for i in spec.get("invariants", ())
    )
    return Entity(key=spec.get("key", "id"), fields=fields, invariants=invariants)


def _local(condition: dict, entity: str) -> dict:
    """`order.status` on an order is `status`. Anything else is left qualified,
    and the caller decides what that means."""
    c = dict(condition)
    prefix, _, rest = c["field"].partition(".")
    if rest and prefix == entity:
        c["field"] = rest
    return c


TYPES = {"int": "int", "money": "int", "number": "float", "bool": "bool"}
"""A spec field's type, as the type a projected tool's parameter takes. Anything
else — text, id, email, a date — crosses as a string."""


def _inputs(names, entity_name: str, entity: Entity) -> tuple[Input, ...]:
    """The operation's inputs, minus the one naming the row it acts on.

    The key arrives as the tool's first parameter under the entity's own name for
    it, so an input named `<entity>_<key>` is that parameter and not a second one
    — the convention the agent's own argument binding already speaks.

    Every other input is typed from the field it shares a name with, and from
    nothing when it shares a name with none — a spec may take an input the entity
    does not store.
    """
    key_names = {entity.key, f"{entity_name}_{entity.key}"}
    kept = []
    for name in names:
        if name in key_names:
            continue
        field = entity.fields.get(name)
        kept.append(Input(name=name, type=TYPES.get(field.type, "str") if field else "str"))
    return tuple(kept)


def _conditions(
    conditions, entity: str, miss
) -> tuple[tuple[Condition, ...], tuple[SessionCondition, ...]]:
    """Split a spec's conditions into what the row decides and what the caller's
    session decides. One over another entity's field no stand-in can see."""
    kept: list[Condition] = []
    owner: list[SessionCondition] = []
    for raw in conditions:
        c = _local(raw, entity)
        if "." in c["field"]:
            miss(f"condition on {c['field']}", "it reads another entity's field")
        elif "equals_session" in c:
            owner.append(SessionCondition(field=c["field"], session=c["equals_session"]))
        else:
            kept.append(Condition(**c))
    return tuple(kept), tuple(owner)


# ---------------------------------------------------------------- coherence


def _check(world: World) -> None:
    for name, target in world.ontology().items():
        entity, _, field = target.partition(".")
        if entity not in world.entities:
            raise InvalidWorld(f"{name} references unknown entity {entity!r}")
        if field not in world.entities[entity].fields:
            raise InvalidWorld(f"{name} references unknown field {target!r}")

    for entity_name, entity in world.entities.items():
        for invariant in entity.invariants:
            for clause, side in ((invariant.when, "when"), (invariant.then, "then")):
                if clause.field not in entity.fields:
                    raise InvalidWorld(
                        f"{entity_name} invariant {invariant.name!r} ({side}) is about "
                        f"{clause.field!r}, which {entity_name!r} does not have"
                    )

    for system_name, system in world.systems.items():
        for action_name, action in system.actions.items():
            declared = world.entities[action.entity].fields
            for condition in (*action.allowed_when, *action.required_when):
                if condition.field not in declared:
                    raise InvalidWorld(
                        f"{system_name}.{action_name} is conditional on "
                        f"{condition.field!r}, which {action.entity!r} does not have"
                    )

    for entity_name, records in world.records.items():
        if entity_name not in world.entities:
            raise InvalidWorld(f"records declared for unknown entity {entity_name!r}")
        entity = world.entities[entity_name]
        for row in records:
            if entity.key not in row:
                raise InvalidWorld(f"a {entity_name} row has no {entity.key!r}")
            for field_name, spec in entity.fields.items():
                if (
                    spec.type == "enum"
                    and field_name in row
                    and str(row[field_name]) not in spec.values
                ):
                    raise InvalidWorld(
                        f"{entity_name} {row[entity.key]}: {field_name}="
                        f"{row[field_name]!r} is not one of {spec.values}"
                    )
            # Coherence, checked the same way and for the same reason. A seeded
            # row that could not exist produces verdicts about a world nobody
            # meant to write — and unlike a bad enum, nothing downstream notices.
            for invariant in entity.violations(row):
                raise InvalidWorld(
                    f"{entity_name} {row[entity.key]} violates {invariant.name!r}"
                    + (f": {invariant.because}" if invariant.because else "")
                )

            # Referential integrity, checked at load rather than discovered when
            # a scenario asks a question whose answer does not exist.
            for field_name, target in entity.refs().items():
                other, _, other_key = target.partition(".")
                known = {r[other_key] for r in world.records.get(other, ())}
                if field_name in row and row[field_name] not in known:
                    raise InvalidWorld(
                        f"{entity_name} {row[entity.key]}: {field_name}="
                        f"{row[field_name]!r} matches no {other}"
                    )


__all__ = ["API_VERSION", "InvalidWorld", "WorldFile", "compose", "load", "resolve_spec"]
