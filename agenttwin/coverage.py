"""Which scenarios to run, and which an agent's suite is missing (T-109).

    python -m agenttwin coverage path/to/agent.aoas.yaml scenarios/ [more/]
    python -m agenttwin scaffold path/to/agent.aoas.yaml --out . --pairwise

`scaffold` writes about one scenario per operation and per refusal. That is the
first draft, and it says nothing about the conversations that go wrong because
two things met: a customer who will not give the number *and* a shipped order,
a cancellation *and* a reply that never came back. This module counts those
meetings and writes the ones nobody has.

**Two measures, both from the AOAS alone.**

1. **Pairs.** Four dimensions — the intent a customer arrives with (`intents`),
   the state the row they ask about is in (`state_machines`), how the customer
   behaves (the persona catalogue, `agenttwin.personas`) and what goes wrong
   that is nobody's fault (the perturbation kinds of the scenario format). Every
   value of one dimension must meet every value of every other in at least one
   scenario. A pair that cannot exist is not counted: an intent no operation
   serves has no row to be in a state, and no tool for a fault to land on.
2. **Transitions.** Every transition of every state machine, walked at least
   once. The walker is GraphWalker's edge-coverage technique, implemented here
   and not imported: from the first state, the shortest way to the nearest
   transition not yet walked, again until none is left.

**What a scenario covers is read, never run.** The intent from `intent:<name>`
in `discharges` where a scenario says so, else from a line the customer says
that is one of the intent's examples, a refusal or escalation rule it
discharges, or the operations its script calls (writes before reads). The state
from the world rows the scenario names. A transition from a row that starts in
its `from` state and is either checked to end in its `to` state, called through
the operation that moves it (whose preconditions hold on the row, and which no
check says never lands), or moved there by a stale read or a calendar entry. It
is a static reading and it says so: the runner is what proves a scenario does
what it claims.

**The pairwise choice is a seam.** `Covering` is any function that turns
dimensions and a feasibility rule into rows covering every feasible pair;
`agenttwin.pairwise.allpairs` binds it to allpairspy, outside the core.
"""

from __future__ import annotations

import itertools
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, get_args

import yaml

from agenttwin.loader import load
from agenttwin.personas import PERSONAS
from agenttwin.scenario_file import InvalidScenario, PerturbationFile, ScenarioFile, load_scenario
from agenttwin.spec import load_spec
from agenttwin.world import World

NO_ROW = "-"
"""The state dimension's value when the conversation names no row."""
NO_FAULT = "none"

DIMENSIONS = ("intent", "state", "persona", "perturbation")
PERTURBATIONS: tuple[str, ...] = (
    NO_FAULT,
    *get_args(PerturbationFile.model_fields["kind"].annotation),
)
ON_A_TOOL = frozenset(k for k in PERTURBATIONS if k != NO_FAULT and not k.startswith("provider_"))

Covering = Callable[[Mapping[str, Sequence[Any]], Callable[[Mapping[str, Any]], bool]], list[dict]]
"""`(dimensions, allowed) -> rows`. Bound by `agenttwin.pairwise.allpairs`."""

Pair = tuple[tuple[str, str], tuple[str, str]]
"""`((dimension, value), (dimension, value))`, dimensions in `DIMENSIONS` order."""


# ------------------------------------------------------------------ the space


@dataclass(frozen=True)
class Transition:
    machine: str
    entity: str
    field: str
    src: str
    dst: str
    by: str

    @property
    def name(self) -> str:
        return f"{self.machine}: {self.src} -> {self.dst} by {self.by}"


@dataclass(frozen=True)
class Intent:
    name: str
    via: tuple[str, ...]
    answered_by: str
    entities: tuple[str, ...]
    machines: tuple[str, ...]
    refusal: str | None
    example: str


@dataclass
class Space:
    """What an AOAS says can happen: the dimensions, what may meet what, and
    the transitions."""

    doc: dict
    intents: dict[str, Intent]
    states: dict[str, tuple[str, str]]
    """State value → (machine, state). The value is the bare state name, or
    `machine:state` when the AOAS declares more than one machine."""
    transitions: list[Transition]
    machine_fields: dict[str, tuple[str, str]]
    """Machine → (entity, field) holding it."""

    @property
    def dimensions(self) -> dict[str, list[str]]:
        return {
            "intent": list(self.intents),
            "state": [NO_ROW, *self.states],
            "persona": list(PERSONAS),
            "perturbation": list(PERTURBATIONS),
        }

    def allowed(self, combo: Mapping[str, Any]) -> bool:
        """May these values sit in one scenario. Partial combinations are asked
        too: the covering function prunes as it builds."""
        intent = self.intents.get(combo.get("intent", ""))
        state, fault = combo.get("state"), combo.get("perturbation")
        if fault == "stale_read" and state == NO_ROW:
            return False
        if intent is None:
            return True
        if state is not None and state != NO_ROW and self.states[state][0] not in intent.machines:
            return False
        if fault in ON_A_TOOL and not intent.via:
            return False
        return not (fault == "stale_read" and not intent.machines)

    def pairs(self) -> set[Pair]:
        """Every feasible pair."""
        dims = self.dimensions
        return {
            ((a, x), (b, y))
            for a, b in itertools.combinations(DIMENSIONS, 2)
            for x in dims[a]
            for y in dims[b]
            if self.allowed({a: x, b: y})
        }

    def state_name(self, machine: str, state: str) -> str:
        return state if len(self.machine_fields) == 1 else f"{machine}:{state}"

    def machine_of(self, entity: str) -> str | None:
        return next((m for m, (e, _) in self.machine_fields.items() if e == entity), None)

    def routes(self, op: str) -> set[str]:
        """The operation itself and whatever it routes to (`request_refund` →
        `issue_refund`)."""
        target = (self.doc.get("operations", {}).get(op) or {}).get("routes_to")
        return {op, target} if target else {op}


def space(doc: dict) -> Space:
    machines = doc.get("state_machines") or {}
    entities = doc.get("entities") or {}
    operations = doc.get("operations") or {}

    machine_fields: dict[str, tuple[str, str]] = {}
    for ename, entity in entities.items():
        for fname, spec in (entity.get("fields") or {}).items():
            if isinstance(spec, dict) and spec.get("of") in machines:
                machine_fields.setdefault(spec["of"], (ename, fname))

    transitions: list[Transition] = []
    for mname, machine in machines.items():
        if mname not in machine_fields:
            continue
        entity, fname = machine_fields[mname]
        states = list(machine.get("states") or [])
        terminal = set(machine.get("terminal") or [])
        for t in machine.get("transitions") or []:
            src = t["from"]
            sources = (
                [s for s in states if s not in terminal]
                if src == "any_except_terminal"
                else states
                if src == "any"
                else [src]
                if isinstance(src, str)
                else list(src)
            )
            for s in sources:
                if s != t["to"]:
                    transitions.append(Transition(mname, entity, fname, s, t["to"], t["by"]))

    sp = Space(
        doc=doc, intents={}, states={}, transitions=transitions, machine_fields=machine_fields
    )
    for mname in machine_fields:
        for s in machines[mname].get("states") or []:
            sp.states[sp.state_name(mname, s)] = (mname, s)

    declared = doc.get("intents") or {
        # No intents declared: one per operation the model is offered.
        f"op:{op}": {"answered_by": "loop", "via": [op]}
        for op, spec in operations.items()
        if spec.get("offered", True)
    }
    for name, spec in declared.items():
        via = tuple(spec.get("via") or ())
        ents = tuple(dict.fromkeys(operations[o]["entity"] for o in via if o in operations))
        sp.intents[name] = Intent(
            name=name,
            via=via,
            answered_by=spec.get("answered_by", "loop"),
            entities=ents,
            machines=tuple(m for m, (e, _) in machine_fields.items() if e in ents),
            refusal=spec.get("refusal"),
            example=(spec.get("examples") or [name.replace("_", " ")])[0],
        )
    return sp


# ------------------------------------------------------------------ the walker


def walk(transitions: Sequence[Transition], start: str | None = None) -> list[list[Transition]]:
    """Paths that walk every transition at least once (edge coverage).

    From `start` (the machine's first state), the shortest way to the nearest
    transition not yet walked, then on from where it ends; when nothing new is
    reachable, a new path from `start`, or from the source of a transition
    `start` cannot reach. Deterministic: ties go to declaration order.
    """
    if not transitions:
        return []
    start = start or transitions[0].src
    left = list(transitions)
    paths: list[list[Transition]] = []
    while left:
        cur = start if _nearest(transitions, start, left) else left[0].src
        path: list[Transition] = []
        while True:
            hop = _nearest(transitions, cur, left)
            if not hop:
                break
            path += hop
            for t in hop:
                if t in left:
                    left.remove(t)
            cur = hop[-1].dst
        paths.append(path)
    return paths


def _nearest(
    transitions: Sequence[Transition], start: str, wanted: Sequence[Transition]
) -> list[Transition]:
    """Breadth first: the shortest sequence from `start` ending on a wanted transition."""
    queue: list[tuple[str, list[Transition]]] = [(start, [])]
    seen = {start}
    while queue:
        state, route = queue.pop(0)
        for t in transitions:
            if t.src != state:
                continue
            if t in wanted:
                return [*route, t]
            if t.dst not in seen:
                seen.add(t.dst)
                queue.append((t.dst, [*route, t]))
    return []


def route_to(transitions: Sequence[Transition], start: str, state: str) -> list[Transition]:
    """The shortest walk from `start` to `state`, for a skeleton's note."""
    queue: list[tuple[str, list[Transition]]] = [(start, [])]
    seen = {start}
    while queue:
        cur, route = queue.pop(0)
        if cur == state:
            return route
        for t in transitions:
            if t.src == cur and t.dst not in seen:
                seen.add(t.dst)
                queue.append((t.dst, [*route, t]))
    return []


# --------------------------------------------------------------- what is there


@dataclass
class Observed:
    """What one scenario covers, read from its file."""

    path: Path
    intents: dict[str, str] = field(default_factory=dict)
    """Intent → the evidence it was read from."""
    states: dict[str, tuple[str, str]] = field(default_factory=dict)
    """Row key → (entity, state value) the conversation meets."""
    persona: str = "plain"
    perturbations: tuple[str, ...] = (NO_FAULT,)
    transitions: set[Transition] = field(default_factory=set)
    skipped: str = ""

    def combos(self, sp: Space) -> list[dict[str, str]]:
        out = []
        for intent in self.intents:
            machines = sp.intents[intent].machines
            states = [s for _, s in self.states.values() if sp.states[s][0] in machines] or [NO_ROW]
            for state, fault in itertools.product(dict.fromkeys(states), self.perturbations):
                out.append(
                    {
                        "intent": intent,
                        "state": state,
                        "persona": self.persona,
                        "perturbation": fault,
                    }
                )
        return out


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


@cache
def _world(path: Path) -> World:
    return load(path)


def observe(path: Path, sp: Space) -> Observed:
    """Read what one scenario covers. A scenario on another agent's world, or
    one that does not load, is skipped and says why."""
    seen = Observed(path=path)
    try:
        scenario = load_scenario(path)
    except InvalidScenario as exc:
        seen.skipped = f"does not load: {str(exc).splitlines()[0][:120]}"
        return seen
    raw = yaml.safe_load(path.read_text())
    world = _world((path.parent / scenario.world).resolve())
    agent = sp.doc["agent"]["id"]
    if world.spec is None or world.spec.aoas != agent:
        seen.skipped = (
            f"its world cites {world.spec.aoas if world.spec else 'no spec'}, not {agent}"
        )
        return seen

    seen.persona = scenario.actor.persona if scenario.actor.kind == "model" else "plain"
    seen.perturbations = tuple(dict.fromkeys(p.kind for p in scenario.perturbations)) or (NO_FAULT,)
    seen.intents = _intents(scenario, sp)
    _rows(scenario, raw, world, sp, seen)
    return seen


def _intents(scenario: ScenarioFile, sp: Space) -> dict[str, str]:
    found: dict[str, str] = {}
    for d in scenario.discharges:
        if d.startswith("intent:") and d[7:] in sp.intents:
            found[d[7:]] = "declared"
    if found:
        return found
    said = [
        _norm(s)
        for s in (*scenario.actor.says, scenario.actor.opening, scenario.actor.situation)
        if s
    ]
    examples = sp.doc.get("intents") or {}
    for name in sp.intents:
        if any(_norm(e) in said for e in (examples.get(name) or {}).get("examples") or []):
            found[name] = "an example said"
    for d in scenario.discharges:
        for name, intent in sp.intents.items():
            if intent.refusal and d == intent.refusal:
                found.setdefault(name, f"discharges {d}")
            if d.startswith("esc:") and name.replace("_", "-") in d:
                found.setdefault(name, f"discharges {d}")
    # The operations it exercises: discharged, scripted, or checked to happen.
    # A check that a call or effect never happens is not evidence of the intent — a
    # scenario about a stranger expects no payout without anyone asking for one.
    ops: list[str] = [d[3:] for d in scenario.discharges if d.startswith("op:")]
    for turn in scenario.model:
        for call in turn.calls or ():
            ops += list(call)
    ops += [c.effect or c.called or "" for c in scenario.expect if c.times != 0]
    operations = sp.doc.get("operations") or {}
    ops = [o for o in dict.fromkeys(ops) if o in operations]
    writes = [o for o in ops if operations[o].get("side_effect") != "read"]
    # Writes always count; reads only when nothing else says what was asked.
    for group in (writes, [o for o in ops if o not in writes]):
        if found and group is not writes:
            break
        for op in group:
            serving = [n for n, i in sp.intents.items() if any(op in sp.routes(v) for v in i.via)]
            if serving:
                found.setdefault(
                    serving[0],
                    f"calls {op}" + (f" (also {', '.join(serving[1:])})" if serving[1:] else ""),
                )
    return found


def _rows(scenario: ScenarioFile, raw: dict, world: World, sp: Space, seen: Observed) -> None:
    text = list(_strings(raw))
    start: dict[str, tuple[str, str, dict]] = {}  # key -> (machine, state, row)
    for machine, (entity, fname) in sp.machine_fields.items():
        key_field = world.entities[entity].key if entity in world.entities else "id"
        for row in world.records.get(entity, ()):
            key = str(row.get(key_field))
            pattern = re.compile(rf"(?<![\w-]){re.escape(key)}(?![\w-])", re.IGNORECASE)
            if row.get(fname) in sp.doc["state_machines"][machine]["states"] and any(
                pattern.search(s) for s in text
            ):
                start[key] = (machine, row[fname], dict(row))

    def moved(key: str, src: str, dst: str, by: str | None = None) -> None:
        machine = start[key][0]
        for t in sp.transitions:
            if (
                t.machine == machine
                and t.src == src
                and t.dst == dst
                and (by is None or t.by == by)
            ):
                seen.transitions.add(t)

    state: dict[str, str] = {k: s for k, (_, s, _) in start.items()}
    for entry in scenario.calendar:
        if entry.record_only:
            continue
        keys = [
            str(v)
            for c in entry.where
            if c.get("field") in ("id", "key", "ticket")
            for v in c.get("equals") or ()
        ]
        for key in keys:
            if key not in start:
                continue
            machine = start[key][0]
            fname = sp.machine_fields[machine][1]
            if fname in entry.sets:
                moved(key, state[key], str(entry.sets[fname]), "external")
                state[key] = str(entry.sets[fname])

    for key, s in state.items():
        machine, _, row = start[key]
        seen.states[key] = (sp.machine_fields[machine][0], sp.state_name(machine, s))

    for p in scenario.perturbations:
        if p.kind == "stale_read" and p.key in state:
            fname = sp.machine_fields[start[p.key][0]][1]
            if fname in p.sets:
                moved(p.key, state[p.key], str(p.sets[fname]), "external")

    never = {(c.effect, c.key) for c in scenario.expect if c.effect and c.times == 0}
    for c in scenario.expect:
        if (
            c.row
            and c.id in state
            and c.equals is not None
            and c.field == sp.machine_fields[start[c.id][0]][1]
        ):
            moved(c.id, state[c.id], str(c.equals))

    for turn in scenario.model:
        for call in turn.calls or ():
            for op, args in call.items():
                for key in {str(v) for v in _strings(args)} & set(state):
                    machine, _, row = start[key]
                    row = {**row, sp.machine_fields[machine][1]: state[key]}
                    for target in sp.routes(op):
                        if (target, None) in never or (target, key) in never:
                            continue
                        if not all(_allows(world, o, row) for o in {op, target}):
                            continue
                        moved_by = [
                            t
                            for t in sp.transitions
                            if t.machine == machine and t.src == state[key] and t.by == target
                        ]
                        seen.transitions.update(moved_by)


def _allows(world: World, op: str, row: dict) -> bool:
    found = world.action(op)
    return found is None or found[1].evaluate(row)[0]


# ------------------------------------------------------------------ the report


@dataclass
class Report:
    agent: str
    space: Space
    observed: list[Observed]
    pairs: set[Pair]
    covered_pairs: set[Pair]
    covered_transitions: set[Transition]

    @property
    def missing_pairs(self) -> set[Pair]:
        return self.pairs - self.covered_pairs

    @property
    def missing_transitions(self) -> list[Transition]:
        return [t for t in self.space.transitions if t not in self.covered_transitions]

    def summary(self) -> dict[str, Any]:
        by_dims: Counter[str] = Counter()
        for (a, _), (b, _) in self.missing_pairs:
            by_dims[f"{a} x {b}"] += 1
        read = [o for o in self.observed if not o.skipped]
        return {
            "agent": self.agent,
            "scenarios": {
                "read": len(read),
                "skipped": len(self.observed) - len(read),
                "no_intent": sum(1 for o in read if not o.intents),
            },
            "dimensions": {k: len(v) for k, v in self.space.dimensions.items()},
            "pairs": {"covered": len(self.covered_pairs), "total": len(self.pairs)},
            "missing_pairs_by_dimensions": dict(by_dims.most_common()),
            "transitions": {
                "covered": len(self.covered_transitions),
                "total": len(self.space.transitions),
            },
            "missing_transitions": [t.name for t in self.missing_transitions],
            "walker_paths": [
                [t.name for t in p]
                for m in self.space.machine_fields
                for p in walk(
                    [t for t in self.space.transitions if t.machine == m],
                    self.space.doc["state_machines"][m]["states"][0],
                )
            ],
        }


def report(aoas: Path, scenarios: Iterable[Path]) -> Report:
    doc = load_spec(aoas)
    sp = space(doc)
    observed = [observe(p.resolve(), sp) for p in sorted(scenarios)]
    covered: set[Pair] = set()
    for o in observed:
        for combo in o.combos(sp):
            covered |= _pairs_of(combo)
    pairs = sp.pairs()
    return Report(
        agent=doc["agent"]["id"],
        space=sp,
        observed=observed,
        pairs=pairs,
        covered_pairs=covered & pairs,
        covered_transitions=set().union(*(o.transitions for o in observed)) if observed else set(),
    )


def _pairs_of(combo: Mapping[str, str]) -> set[Pair]:
    return {
        ((a, combo[a]), (b, combo[b]))
        for a, b in itertools.combinations(DIMENSIONS, 2)
        if a in combo and b in combo
    }


def scenario_files(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        out += sorted(p.glob("*.yaml")) if p.is_dir() else [p]
    return out


def render(rep: Report, *, top: int = 10) -> str:
    s = rep.summary()
    lines = [
        f"coverage of {s['agent']}",
        f"  scenarios read {s['scenarios']['read']}, skipped {s['scenarios']['skipped']}, "
        f"no intent found in {s['scenarios']['no_intent']}",
        "  dimensions " + " · ".join(f"{k} {v}" for k, v in s["dimensions"].items()),
        f"  pairs        {s['pairs']['covered']}/{s['pairs']['total']}",
        f"  transitions  {s['transitions']['covered']}/{s['transitions']['total']}",
        "  missing pairs, by dimensions:",
        *(f"    {k:<26} {v}" for k, v in s["missing_pairs_by_dimensions"].items()),
        "  missing transitions:",
        *(f"    {t}" for t in s["missing_transitions"]),
    ]
    gaps = _top_missing(rep, top)
    if gaps:
        lines.append(f"  top missing values (in the most missing pairs, first {top}):")
        lines += [f"    {d}={v:<24} {n} pairs" for (d, v), n in gaps]
    for o in rep.observed:
        if o.skipped:
            lines.append(f"  skipped {o.path.name}: {o.skipped}")
    return "\n".join(lines)


def _top_missing(rep: Report, top: int) -> list[tuple[tuple[str, str], int]]:
    count: Counter[tuple[str, str]] = Counter()
    for a, b in rep.missing_pairs:
        count[a] += 1
        count[b] += 1
    return count.most_common(top)


# ------------------------------------------------------------------ skeletons


HEADER = (
    "# Scaffolded by `python -m agenttwin scaffold --pairwise`. "
    "TODO: say it in the customer's words.\n"
)


def choose(
    sp: Space, covering: Covering, already: set[Pair] | None = None
) -> tuple[list[dict[str, str]], int]:
    """The combinations to write: the covering function's rows that add a pair
    nothing covers yet, then rows of our own for any feasible pair it left out.

    The top-up is not optional. allpairspy's filter is greedy and gives up on a
    row it cannot extend, and with the AOAS's constraints it left 187 of the
    clothing agent's 468 feasible pairs uncovered (2026-10-11). Each top-up row
    starts from one missing pair and fills the other dimensions with the
    allowed value that covers the most pairs still missing, ties to
    declaration order. Returns the rows and how many the top-up added.
    """
    pairs = sp.pairs()
    covered = set(already or ())
    rows: list[dict[str, str]] = []
    for combo in covering(sp.dimensions, sp.allowed):
        new = (_pairs_of(combo) & pairs) - covered
        if new:
            covered |= new
            rows.append(dict(combo))
    from_covering = len(rows)
    dims = sp.dimensions
    for pair in sorted(pairs - covered):
        if pair in covered:
            continue
        row = dict(pair)
        for d in DIMENSIONS:
            if d in row:
                continue
            options = [v for v in dims[d] if sp.allowed({**row, d: v})]
            row[d] = max(
                options,
                key=lambda v: (
                    len((_pairs_of({**row, d: v}) & pairs) - covered),
                    -dims[d].index(v),
                ),
            )
        covered |= _pairs_of(row) & pairs
        rows.append(row)
    return rows, len(rows) - from_covering


def skeletons(
    rep: Report, world_path: Path, scenarios_dir: Path, covering: Covering
) -> dict[str, str]:
    """Scenario files for what the report says is missing: combinations that
    each add a pair no scenario covers (`choose`), and one per transition
    nothing walks. Every file loads; what only a person can know — the
    customer's words, the expected effects — is marked TODO, as `scaffold`
    marks it."""
    sp = rep.space
    world = load(world_path)
    world_rel = _rel(world_path, scenarios_dir)
    out: dict[str, str] = {}
    rows, _ = choose(sp, covering, rep.covered_pairs)
    for combo in rows:
        name = "pairwise-" + "-".join(_slug(combo[d]) for d in DIMENSIONS)
        out[name] = _pairwise(sp, world, world_rel, combo)
    for t in rep.missing_transitions:
        out[f"path-{_slug(t.machine)}-{_slug(t.src)}-to-{_slug(t.dst)}"] = _path(
            sp, world, world_rel, t
        )
    return out


def _rel(path: Path, base: Path) -> str:
    import os

    return os.path.relpath(path.resolve(), base.resolve())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "none"


def _customer_field(sp: Space, world: World, entity: str) -> str | None:
    session = sp.doc.get("session") or {}
    target = next((s["ref"] for s in session.values() if isinstance(s, dict) and s.get("ref")), "")
    return next((f for f, t in world.entities[entity].refs().items() if t == target), None)


def _a_row(sp: Space, world: World, machine: str, state: str) -> tuple[str, str, list[dict]]:
    """A row of the machine's entity in `state`, its owner, and the t0 calendar
    that puts it there when the world holds no such row."""
    entity, fname = sp.machine_fields[machine]
    key_field = world.entities[entity].key
    owner = _customer_field(sp, world, entity)
    rows = list(world.records.get(entity, ()))
    pick = next(
        (r for r in rows if r.get(fname) == state and (owner is None or r.get(owner))), None
    )
    if pick is not None:
        return str(pick[key_field]), str(pick.get(owner) or _first_customer(sp, world)), []
    base = next((r for r in rows if owner is None or r.get(owner)), None)
    if base is None:
        return "TODO", _first_customer(sp, world), []
    key = str(base[key_field])
    plant = [
        {
            "id": f"put-{_slug(key)}-at-{_slug(state)}",
            "entity": entity,
            "where": [{"field": key_field, "equals": [key]}],
            "sets": {fname: state},
            "by": "scaffold setup (TODO: check the row's other fields agree)",
        }
    ]
    return key, str(base.get(owner) or _first_customer(sp, world)), plant


def _first_customer(sp: Space, world: World) -> str:
    session = sp.doc.get("session") or {}
    ref = next((s["ref"] for s in session.values() if isinstance(s, dict) and s.get("ref")), "")
    entity = ref.partition(".")[0]
    rows = world.records.get(entity, ())
    return str(rows[0][world.entities[entity].key]) if rows else "TODO"


def _args(sp: Space, world: World, op: str, key: str) -> dict[str, Any]:
    found = world.action(op)
    if found is None:
        return {"id": key}
    action = found[1]
    args: dict[str, Any] = {} if action.many else {world.entities[action.entity].key: key}
    for i in action.inputs:
        args.setdefault(i.name, "TODO")
    return args


def _dump(body: dict, header: str) -> str:
    return header + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=88)


def _pairwise(sp: Space, world: World, world_rel: str, combo: Mapping[str, str]) -> str:
    intent = sp.intents[combo["intent"]]
    state, persona, fault = combo["state"], combo["persona"], combo["perturbation"]
    key = entity = None
    calendar: list[dict] = []
    as_ = _first_customer(sp, world)
    if state != NO_ROW:
        machine, bare = sp.states[state]
        entity = sp.machine_fields[machine][0]
        key, as_, calendar = _a_row(sp, world, machine, bare)
    offered = [o for o in intent.via if world.action(o) is not None]
    writes = [o for o in offered if world.action(o)[1].side_effect != "read"]  # type: ignore[index]
    reads = [o for o in offered if o not in writes]

    words = f"{intent.example}" + (f" ({key})" if key else "")
    actor: dict[str, Any] = (
        {"says": [words]}
        if persona == "plain"
        else {
            "kind": "model",
            "persona": persona,
            "situation": f"TODO: the situation, in the customer's terms — {words}",
        }
    )
    body: dict[str, Any] = {
        "apiVersion": "awd-scenario/v0",
        "scenario": f"{combo['intent']} · {state} · {persona} · {fault}",
        "world": world_rel,
        "as": as_,
        "objective": f"TODO: what must hold when {combo['intent']} meets {state}, "
        f"a {persona} customer and {fault}.",
        "discharges": [f"intent:{combo['intent']}", *(f"op:{o}" for o in offered)]
        + ([intent.refusal] if intent.refusal else []),
        "max_turns": 1 if persona == "plain" else 4,
    }
    if calendar:
        body["calendar"] = calendar
    body["actor"] = actor
    if fault != NO_FAULT:
        body["perturbations"] = [_perturbation(sp, world, fault, intent, key, entity, state)]
    calls = [{o: _args(sp, world, o, key or "TODO")} for o in (writes or reads)[:1]]
    if calls:
        body["model"] = [{"calls": calls}, {"says": "TODO: what the model says.", "times": 2}]
    body["expect"] = (
        [{"truthful": entity, "id": key}] if key and entity else [{"world": "unchanged"}]
    )
    note = [
        f"# Pairwise: intent={combo['intent']} · state={state} · persona={persona} · "
        f"perturbation={fault}.",
        "# TODO: the expected effects — what must change and what must not"
        + (", e.g." if writes else "."),
    ]
    for o in writes:
        note.append(f"#   - {{effect: {o}{', key: ' + key if key else ''}, times: 0 or 1}}")
    if calendar:
        note.append(f"# No row in the world is {state}: the calendar puts {key} there at t0.")
    if any(v == "TODO" for c in calls for a in c.values() for v in a.values()):
        note.append("# Inputs marked TODO are not fields of the row; give them real values.")
    return _dump(body, HEADER + "\n".join(note) + "\n")


def _perturbation(
    sp: Space,
    world: World,
    fault: str,
    intent: Intent,
    key: str | None,
    entity: str | None,
    state: str,
) -> dict[str, Any]:
    if fault.startswith("provider_"):
        return {"kind": fault}
    offered = [o for o in intent.via if world.action(o) is not None] or list(intent.via)
    writes = [
        o
        for o in offered
        if (world.action(o) or ("", None))[1] is not None
        and world.action(o)[1].side_effect != "read"
    ]  # type: ignore[index]
    if fault == "stale_read" and key and entity:
        machine, bare = sp.states[state]
        reads = [
            op
            for s in world.systems.values()
            for op, a in s.actions.items()
            if a.entity == entity and a.side_effect == "read" and not a.many
        ]
        nxt = next((t.dst for t in sp.transitions if t.machine == machine and t.src == bare), None)
        return {
            "kind": "stale_read",
            "tool": (reads or offered)[0],
            "at_call": 1,
            "entity": entity,
            "key": key,
            "sets": {sp.machine_fields[machine][1]: nxt or bare},
        }
    return {"kind": fault, "tool": (writes or offered)[0], "at_call": 1}


def _path(sp: Space, world: World, world_rel: str, t: Transition) -> str:
    key, as_, calendar = _a_row(sp, world, t.machine, t.src)
    start = sp.doc["state_machines"][t.machine]["states"][0]
    walked = route_to(sp.transitions, start, t.src)
    reach = " -> ".join([start, *(w.dst for w in walked)]) if walked else start
    body: dict[str, Any] = {
        "apiVersion": "awd-scenario/v0",
        "scenario": f"{t.entity} {key} moves {t.src} -> {t.dst} by {t.by}",
        "world": world_rel,
        "as": as_,
        "objective": f"TODO: what must hold once {t.entity} {key} has moved "
        f"from {t.src} to {t.dst}.",
    }
    reads = [
        op
        for s in world.systems.values()
        for op, a in s.actions.items()
        if a.entity == t.entity and a.side_effect == "read" and not a.many
    ]
    if t.by == "external":
        writers = sp.doc["state_machines"][t.machine].get("concurrent_writers") or ["external"]
        body["discharges"] = [f"transition:{t.machine}:{t.src}->{t.dst}"]
        body["max_turns"] = 1
        body["calendar"] = [
            *calendar,
            {
                "id": f"{_slug(t.src)}-to-{_slug(t.dst)}",
                "entity": t.entity,
                "where": [{"field": world.entities[t.entity].key, "equals": [key]}],
                "sets": {t.field: t.dst},
                "by": str(writers[0]),
            },
        ]
        body["actor"] = {"says": [f"what is happening with {key}"]}
        if reads:
            body["model"] = [
                {"calls": [{reads[0]: _args(sp, world, reads[0], key)}]},
                {"says": "TODO: what the model says.", "times": 2},
            ]
    else:
        op = t.by
        offered = [
            o
            for o, spec in (sp.doc.get("operations") or {}).items()
            if spec.get("routes_to") == t.by and spec.get("offered", True)
        ]
        if (sp.doc.get("operations") or {}).get(t.by, {}).get("offered") is False and offered:
            op = offered[0]
        body["discharges"] = [f"op:{t.by}", f"transition:{t.machine}:{t.src}->{t.dst}"]
        body["max_turns"] = 1
        if calendar:
            body["calendar"] = calendar
        body["actor"] = {"says": [f"please {op.replace('_', ' ')} {key}"]}
        body["model"] = [
            {"calls": [{op: _args(sp, world, op, key)}]},
            {"says": "TODO: what the model says.", "times": 2},
        ]
    body["expect"] = [
        {"row": t.entity, "id": key, "field": t.field, "equals": t.dst},
        {"truthful": t.entity, "id": key},
    ]
    note = [
        f"# Transition {t.name} — walked by no scenario.",
        f"# The walker reaches {t.src} from {start} by: {reach}."
        if walked
        else f"# {t.src} is where the machine starts.",
    ]
    if t.by != "external":
        note.append(
            f"# TODO: if {t.by} needs a person's approval here, add an approver; and the"
            " effects that must or must not land."
        )
    if calendar:
        note.append(f"# No row in the world is {t.src}: the calendar puts {key} there at t0.")
    return _dump(body, HEADER + "\n".join(note) + "\n")


def write_skeletons(
    files: Mapping[str, str], scenarios_dir: Path, *, force: bool = False
) -> list[Path]:
    """Write, then load each the way the runner will."""
    written = []
    for name, text in files.items():
        path = scenarios_dir / f"{name}.yaml"
        if path.exists() and not force:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written.append(path)
    for path in written:
        load_scenario(path)
    return written


__all__ = [
    "DIMENSIONS",
    "NO_FAULT",
    "NO_ROW",
    "PERTURBATIONS",
    "Covering",
    "Intent",
    "Observed",
    "Report",
    "Space",
    "Transition",
    "choose",
    "observe",
    "render",
    "report",
    "route_to",
    "scenario_files",
    "skeletons",
    "space",
    "walk",
    "write_skeletons",
]
