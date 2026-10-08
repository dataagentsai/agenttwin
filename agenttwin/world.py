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

    untrusted: bool = False
    """Written by somebody other than the system — content, never instruction.

    Carried because a world has to know where an attack may legitimately be
    planted: an injection belongs in a field a customer or a warehouse writes,
    and planting one in text the system itself produces tests a threat nobody
    faces.
    """

    pii: bool = False
    """Personal data. Declared here so redaction and leak checks read one
    statement rather than each keeping a list."""

    advances: Literal["days"] | None = None
    """A counter that runs with world time.

    Without it a world is frozen: `days_since_delivery` is whatever was seeded
    and a return window can never close mid-conversation, so the one rule this
    agent argues about most is the one no simulation can reach. A scenario that
    advances the world by days advances every field declared here.
    """

    pattern: str | None = None
    """How a value of this field is written, as a regular expression — an id
    customers type (`POL-[0-9]{6}`). The scaffold makes keys that match it, so
    a world's ids look like the ones the agent will be asked about (T-099)."""

    fresh_for_s: int | None = None
    """How long a read of this field stays usable, in seconds.

    Declared where something else writes the field while a conversation is open;
    `None` says a read of it never goes stale in the sense that matters. It is
    the *harness* that acts on this — re-reading before an irreversible action
    whose preconditions reach the field (AHC-0107) — and a world only carries it,
    because a stand-in that decided when its own answers expired would be
    deciding the agent's policy for it.
    """

    advances_when: Condition | None = None
    """When the counter runs — because most of them do not always.

    An order that has not been delivered is not *n* days since delivery; it has
    no age at all. Advancing it anyway produces a row the world declares
    impossible, and the invariant that says so is usually already written: this
    is that same condition, pointed forwards. Absent means the counter always
    runs.
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


Field_.model_rebuild()  # `advances_when` is a Condition, declared below it


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


class _Known(dict):  # type: ignore[type-arg]
    """A row for a refusal template: a field it does not carry reads as unknown."""

    def __missing__(self, field: str) -> str:
        return "unknown"


class Creates(BaseModel):
    """A row of another entity an action brings into being (T-100).

    Registering a claim acts on a policy and makes a claim: the AOAS could only
    set fields on the row an operation acts on, so the claim never appeared and
    its reference never came back."""

    model_config = ConfigDict(frozen=True)

    entity: str
    sets: dict[str, Any] = Field(default_factory=dict)
    sets_from_input: dict[str, str] = Field(default_factory=dict)
    from_row: dict[str, str] = Field(default_factory=dict)
    """New row's field → a field of the row the action acts on."""


class Action(BaseModel):
    """A tool the world exposes, and when it is allowed.

    `refusal` is a template rather than a sentence so a refusal can name the
    state that caused it. A refusal the customer cannot act on is a refusal that
    generates a second contact.
    """

    model_config = ConfigDict(frozen=True)

    entity: str
    side_effect: Literal["read", "reversible", "irreversible"] = "read"
    many: bool = False
    """A read of many rows (`output: entity[]` in the spec): no key, and every row
    the caller's session may see. `session_when` is its whole scope, which is why
    the spec validator refuses a many-read without one."""
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
    agent_when: tuple[Condition, ...] = ()
    """When the agent may do this **alone**, rather than when it is possible.

    A third question, and it was the one nothing carried. `allowed_when` says
    what the world permits anybody to do; `required_when` says when it is owed.
    Neither says who may decide — and an operation the world permits, that is
    not owed, and that the agent may not authorise on its own is the ordinary
    shape of anything involving money.

    Declared under `authority.agent_when` and, until this field existed,
    dropped on load. The cost of dropping it is quiet: a threshold nobody can
    generate a case for is a threshold nothing tests, and the one in the
    reference agent — "above ₹10,000 needs a person" — had never once been
    evaluated as true, because no order in its world was worth enough to reach
    it.
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

    creates: Creates | None = None
    """A row of another entity this action brings into being, if any (T-100)."""

    sets_from_input: dict[str, str] = Field(default_factory=dict)
    """Field → the input whose value it takes. The spec writes these `$name`;
    only a declared input can fill one, and an effect naming anything else stays
    unenforced rather than silently doing nothing."""

    def evaluate(self, row: dict) -> tuple[bool, str]:
        for condition in self.allowed_when:
            if not condition.holds(row):
                # format_map with a default: a refusal naming a field this row
                # does not carry still refuses. A KeyError here turned a refusal
                # into a tool crash when a template outgrew an older row.
                return False, self.refusal.format_map(_Known(row))
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
