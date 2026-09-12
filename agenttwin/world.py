"""The world, as data.

AgentTwin twins the agent's **world**, not the agent. The agent under test is
real; its environment is the twin.

A world declares the rows that exist at t₀, what it is faithful about, and
which of the agent's external systems it stands in for. **It does not declare
the domain.** Entities, operations and eligibility policy are the agent's
specification (AOAS), and a world *cites* that specification rather than
restating it — so an agent spec and a world that disagree cannot exist, because
there is only one statement of each rule.

The agent does not know the return window. The tool server does not know it
either: it reads it from the specification the world cites. A rule that lives
in one declarative place can still be varied per scenario, which is what makes
"return on day 31" a case you write rather than a fixture you edit.

`World` is the composed, in-memory result — the agent spec's entities and
operations, projected through the world's systems. `loader.py` builds it.

## Fidelity is per-property

A world states what it is faithful *about*. Chasing global realism converts a
tractable problem into an infinite one — a cancellation test needs `status` to
be exactly right and needs nothing at all from plausible product copy. Asserting
outside the declared fidelity is a defect in the scenario, not in the world.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Resolution = Literal["mock", "replay", "real", "shadow"]


class Fidelity(BaseModel):
    """What this world claims to be true about, and what it does not.

    `not_faithful_about` is the more useful half. A world that lists only its
    strengths invites assertions it cannot support, and the scenario that makes
    one fails for a reason nobody can act on.
    """

    model_config = ConfigDict(frozen=True)

    faithful_about: tuple[str, ...] = ()
    not_faithful_about: tuple[str, ...] = ()
    verified_against: str | None = None
    """How the claim was checked, if it was. `None` means asserted, not tested —
    which is honest and is exactly what `shadow` mode exists to fix."""


class Field_(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal[
        "id",
        "text",
        "email",
        "phone",
        "int",
        "number",
        "money",
        "bool",
        "date",
        "timestamp",
        "enum",
    ] = "text"
    values: tuple[str, ...] = ()
    ref: str | None = None
    """`entity.field` — a declared join.

    This is the ontology, stated once. Types give you shape; only a `ref` gives
    you identity, and identity is what makes two systems' rows the same row.
    """


class Entity(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str = "id"
    fields: dict[str, Field_] = Field(default_factory=dict)
    invariants: tuple[Invariant, ...] = ()
    """Coherence rules the entity owns.

    On the entity rather than the world because they are statements about one
    row, and putting them here means the thing that declares a field also
    declares what that field can coexist with.
    """

    def refs(self) -> dict[str, str]:
        return {n: f.ref for n, f in self.fields.items() if f.ref}

    def violations(self, row: dict) -> tuple[Invariant, ...]:
        """Every invariant this row breaks. Empty means the row could exist."""
        return tuple(i for i in self.invariants if i.violated_by(row))


class Condition(BaseModel):
    """One clause of an eligibility rule — or of an invariant."""

    model_config = ConfigDict(frozen=True)

    field: str
    equals: tuple[Any, ...] | None = None
    not_equals: tuple[Any, ...] | None = None
    at_most: int | float | None = None
    at_least: int | float | None = None
    """Numbers, not integers — a money bound has to compare. The agent spec's
    condition schema says the same, and one vocabulary with two dialects is the
    duplication this format exists to remove."""

    def holds(self, row: dict) -> bool:
        value = row.get(self.field)
        if self.equals is not None and value not in self.equals:
            return False
        if self.not_equals is not None and value in self.not_equals:
            return False
        if self.at_most is not None and value is not None and value > self.at_most:
            return False
        return not (self.at_least is not None and value is not None and value < self.at_least)


class SessionCondition(BaseModel):
    """A row may be touched only by the caller it belongs to.

    Compares a field of the row with a field of the caller's session — the one
    comparison the constant-valued `Condition` cannot express, and the rule
    behind F-016. Fails closed: no session means no match.
    """

    model_config = ConfigDict(frozen=True)

    field: str
    session: str

    def holds(self, row: dict, session: Mapping[str, object] | None) -> bool:
        if session is None or session.get(self.session) is None:
            return False
        return row.get(self.field) == session.get(self.session)


class Invariant(BaseModel):
    """Which combinations of a row's own fields can coexist.

    An eligibility `Condition` says what the world *permits*. An invariant says
    what the world can *be*. They are different questions and only the first was
    ever asked here: F-011 found that 12 of 29 generated cases described a world
    that cannot exist — `status: pending` with `days_since_delivery: 30`. Every
    one was type-valid and referentially valid, and 41% of the golden set was
    therefore testing the rule against fiction.

    ## Why this is `Condition → Condition`

    Material implication over the predicate language that already exists. No new
    grammar, and an invariant is therefore checkable anywhere a condition is —
    which is the whole point, because the same declaration has to serve three
    consumers that would otherwise each invent their own:

    - the **generator**, as a forbidden-tuple constraint (see below);
    - the **loader**, so a hand-seeded incoherent row fails at load rather than
      producing verdicts about a world nobody meant to write;
    - a **perturbation**, so injecting a fault cannot quietly leave the world in
      a state it declared impossible.

    ## This is constrained combinatorial testing, which is not new

    The CIT literature has carried *forbidden tuples* for two decades and NIST's
    ACTS takes constraints alongside the parameter space. Our `AllPairs` call was
    the unconstrained version of a solved problem. Naming it correctly is the
    point of R-015: the technique is off the shelf, and what was missing is a way
    to *declare* the constraint next to the world it constrains.

    ## What it deliberately cannot say

    One row, its own fields. `warehouse.unused → zero rows in query_history` is a
    cross-entity invariant and is **not** expressible here; so is anything over a
    time series. Both are named in doc 29 as domain-schema work, and stating the
    limit is better than a grammar that half-supports them.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    when: Condition
    then: Condition
    because: str = ""

    def violated_by(self, row: dict) -> bool:
        """An invariant is silent about a row that lacks the field it speaks of.

        Necessary rather than lenient: the generator evaluates *partial* rows
        while it is still choosing values, and a constraint that fired on absent
        fields would prune combinations before they were built.
        """
        if self.when.field not in row:
            return False
        return self.when.holds(row) and not self.then.holds(row)


Entity.model_rebuild()  # `Entity.invariants` forward-references `Invariant`


class Input(BaseModel):
    """One input a projected tool takes beyond the key it acts on.

    The spec declares an operation's inputs; a stand-in that carried only the key
    could not apply an effect written from one — `change_address` took an order
    and changed nothing, and the address the customer gave went nowhere.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    type: Literal["str", "int", "float", "bool"] = "str"


class Action(BaseModel):
    """A tool the world exposes, and when it is allowed.

    `refusal` is a template rather than a sentence so a refusal can name the
    state that caused it. A refusal the customer cannot act on is a refusal that
    generates a second contact.
    """

    model_config = ConfigDict(frozen=True)

    entity: str
    side_effect: Literal["read", "reversible", "irreversible"] = "read"
    scope: str | None = None
    allowed_when: tuple[Condition, ...] = ()
    session_when: tuple[SessionCondition, ...] = ()
    """Whose rows this action may touch. Checked before anything else, and a
    row the caller may not touch is answered exactly as a row that does not
    exist — a refusal that confirmed it would tell a stranger their guess was
    right."""
    required_when: tuple[Condition, ...] = ()
    """When this action is **owed**, not merely permitted.

    The complement `allowed_when` never had, and the gap doc 31 found: every
    oracle we own detects an action that happened and should not have. None can
    see an action that should have happened and did not.

    A missed refund on a returned order changes nothing, claims nothing, exceeds
    no bound and states no falsehood — so the world diff, the truth check and a
    bounds check all pass. The failure with no evidence needs the world to say
    what was owed, because nothing else in the system knows.
    """
    inputs: tuple[Input, ...] = ()
    """Beyond the key. The spec's declared inputs for this operation, minus the
    one that names the row it acts on."""
    sets: dict[str, Any] = Field(default_factory=dict)
    refusal: str = "that is not possible in its current state"
    """The default names no entity. The format cannot know the domain, and a
    default that said "order" was a domain noun inside it."""
    description: str = ""

    def visible_to(self, row: dict, session: Mapping[str, object] | None) -> bool:
        return all(c.holds(row, session) for c in self.session_when)

    sets_from_input: dict[str, str] = Field(default_factory=dict)
    """Field → the input whose value it takes. The spec writes these `$name`;
    only a declared input can fill one, and an effect naming anything else stays
    unenforced rather than silently doing nothing."""

    def evaluate(self, row: dict) -> tuple[bool, str]:
        for condition in self.allowed_when:
            if not condition.holds(row):
                return False, self.refusal.format(**row)
        return True, "allowed"


class System(BaseModel):
    model_config = ConfigDict(frozen=True)

    binding: Literal["mcp"] = "mcp"
    """Realisation, not world. Carried here until the binding spec exists."""
    resolution: Resolution = "mock"
    projects: str | None = None
    """The agent spec's external system this one stands in for."""
    actions: dict[str, Action] = Field(default_factory=dict)


class SpecRef(BaseModel):
    """Which agent specification a world cites.

    `aoas` and `version` are the citation; `path` is only where to find it. A
    path that leads to a different spec, or a different version of the same one,
    fails at load — a pointer that silently follows a moving target is not a
    citation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    aoas: str
    version: str
    path: str


class Unenforced(BaseModel):
    """A statement in the agent spec that this world cannot enforce, and why.

    Computed, never declared. A world that can only enforce what it can see must
    say what it cannot see — otherwise a precondition the world silently skipped
    reads, in every run against it, as a precondition that held.
    """

    model_config = ConfigDict(frozen=True)

    system: str
    operation: str
    statement: str
    reason: str


class World(BaseModel):
    """Everything a scenario runs against."""

    model_config = ConfigDict(frozen=True)

    name: str
    version: int = 1
    seed: int = 0
    spec: SpecRef | None = None
    fidelity: Fidelity = Fidelity()
    entities: dict[str, Entity] = Field(default_factory=dict)
    systems: dict[str, System] = Field(default_factory=dict)
    records: dict[str, tuple[dict, ...]] = Field(default_factory=dict)
    unenforced: tuple[Unenforced, ...] = ()

    def ontology(self) -> dict[str, str]:
        """Every declared join, flattened. `order.customer_id -> customer.id`.

        Stated once and machine-readable, so a projection does not have to infer
        it and a generator does not have to guess it.
        """
        return {
            f"{name}.{field}": target
            for name, entity in self.entities.items()
            for field, target in entity.refs().items()
        }

    def action(self, name: str) -> tuple[str, Action] | None:
        for system_name, system in self.systems.items():
            if name in system.actions:
                return system_name, system.actions[name]
        return None


__all__ = [
    "Action",
    "Condition",
    "Entity",
    "Fidelity",
    "Field_",
    "Input",
    "Invariant",
    "Resolution",
    "SessionCondition",
    "SpecRef",
    "System",
    "Unenforced",
    "World",
]
