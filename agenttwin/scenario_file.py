"""A scenario as a file: `apiVersion: awd-scenario/v0`.

Scenarios lived in pytest, which welded them to one implementation. A suite that
cannot be pointed at a different agent cannot answer either of the questions this
project exists to ask — *did a regeneration arrive at the same behaviour*, and
*does a different agent built from the same specifications behave the same way*.

So a scenario declares what it drives and what it expects, and the runner is
handed an agent it knows nothing about beyond `handle`.

    apiVersion: awd-scenario/v0
    scenario: a refund that needs a human
    world: worlds/clothing.yaml
    as: C-1042                   # a customer the world declares
    discharges: [op:issue_refund, AHC-0057]
    actor:
      says:
        - please refund my order AB-10003
        - any update?
    approver: {decides: grant, by: ops-7}
    expect:
      - {effect: issue_refund, on: AB-10003, times: 1}
      - {row: order, id: AB-10003, field: status, equals: refunded}

**What a scenario may not do** is name a scope, a model, an endpoint, or a tool
the specification does not declare. Those are the binding's, and a scenario that
named one would run against exactly one implementation, which is the thing being
escaped. The tools it may name are the specification's operations, which every
implementation is offered by the same projected world.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agenttwin.checks import Check
from agenttwin.loader import CalendarFile, InvalidWorld, _check_calendar, _event, _seconds, load
from agenttwin.world import CalendarEvent

API_VERSION = "awd-scenario/v0"


class InvalidScenario(Exception):
    """Said at load, where it is cheap, rather than mid-run."""


class ActorFile(BaseModel):
    """Who is talking. `says` is a script; a rule set is a state machine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["scripted", "state_machine", "model"] = "scripted"
    opens: bool = False
    """The customer opens the conversation before saying anything, and is shown
    whatever the implementation shows on opening. With `says` empty, that opening
    is the reply every check reads."""
    says: tuple[str, ...] = ()
    persona: str = "plain"
    """`model` only: how this customer behaves, from the catalogue. The
    behaviour is universal and lives there; what they want is `situation`
    below, which is this scenario's."""
    situation: str = ""
    """`model` only: what this customer is trying to do, in their own terms and
    never in the agent's. A situation naming a tool is a scenario telling the
    customer how the system works."""
    opening: str = ""
    rules: tuple[dict[str, str], ...] = ()
    persistence: str = ""
    max_turns: int = 6


Concern = Literal[
    "functional-suitability",
    "performance-efficiency",
    "compatibility",
    "interaction-capability",
    "reliability",
    "security",
    "maintainability",
    "flexibility",
    "safety",
    "cost",
]
"""The nine quality characteristics of ISO/IEC 25010:2023, plus `cost` — which
25010 has no characteristic for and which is the property an agent is most
likely to fail silently. Cited, not invented, and spelled the same here as in
the assurance catalog, the harness catalog and an AOAS: the Concern View joins
every artifact in the family on this string."""


class ApproverFile(BaseModel):
    """The offstage reviewer. Acts on the queue between turns, never speaks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decides: Literal["grant", "refuse", "never", "grant-twice"] = "grant"
    by: str = "ops-7"
    after_turns: int = 1
    """Which review pass this person acts on. `1` is the first one — they were
    already at their desk — and is the default because a scenario about
    something else should not also be about a slow reviewer.

    Above one it is a delay, `(after_turns - 1)` turns' worth of time, and it is
    how the approval window is made to close on somebody: a reviewer who arrives
    after the grant expired is a third outcome, distinct from yes and from no,
    and the one most likely to be mishandled.

    Read nowhere until 2026-09-12 (F-036). The field was here, documented, from
    the first version of this format, and every runner dropped it."""


class DeskFile(BaseModel):
    """The offstage colleague. Resolves an escalation, or never comes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolves: Literal["handled", "never"] = "handled"
    by: str = "desk-1"
    after_turns: int = 1
    """Which review pass this colleague acts on — see `ApproverFile.after_turns`.
    Above one is how an escalation is made to lapse before anybody comes."""


class PerturbationFile(BaseModel):
    """Something going wrong that is nobody's fault, scheduled on a named call.

    On a *specific* call rather than randomly: a fault that lands somewhere
    different each run produces a failure nobody can reproduce.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[
        "stale_read",
        "slow",
        "channel_error",
        "decline",
        "lost_reply",
        "provider_throttled",
        "provider_unavailable",
        "provider_malformed",
    ]
    tool: str = ""
    """The tool this fault lands on. Empty for the three `provider_*` kinds,
    which land on the model channel instead."""
    at_call: int = 1

    entity: str = "order"
    key: str = ""
    sets: dict[str, str | int | bool] = Field(default_factory=dict)
    """`stale_read` only: what the world becomes after the read was answered."""

    channel: Literal["execution", "protocol"] = "execution"
    message: str = "injected fault"
    """`channel_error` and `decline`. The model is expected to recover from an
    execution error and rarely can from a protocol one, so which is declared
    matters. A `decline` fires on `at_call` and every call to the tool after it:
    the same refusal however often it is asked (T-095)."""

    seconds: float = 0.05
    """`slow` only. A window opener, never a latency measurement."""

    lasts_s: int = 0
    """`provider_*` only: an outage rather than a blip. Once the fault fires,
    every model call fails the same way until this many seconds of the
    scenario's clock have passed. Zero is one failed call.

    Declared in time, not in calls, because a count of calls is a count of the
    binding's retries: `provider_unavailable` on calls 1 and 2 was meant as two
    failed turns and a resilient agent absorbed both in its first turn's retries
    (reference-agent T-050). An outage that lasts two turns' worth of clock fails
    two turns whatever the retry count."""

    retry_after: float | None = None
    """`provider_throttled` only: what the provider says about coming back.

    **The model provider is an external system the agent depends on**, so a
    world must be able to misbehave as it — throttling, an outage, output that
    does not parse. Declared here by *kind* and never by exception type: which
    error a throttle becomes is the binding's, exactly as a scope name is."""

    @model_validator(mode="after")
    def _tool_where_a_tool_is_meant(self) -> PerturbationFile:
        on_provider = self.kind.startswith("provider_")
        if on_provider and self.tool:
            raise ValueError(f"{self.kind} lands on the model, not on {self.tool!r}")
        if not on_provider and not self.tool:
            raise ValueError(f"{self.kind} needs the tool it lands on")
        return self


class GenerateFile(BaseModel):
    """Many cases from one declaration: a generator, a seed and a count.

    The scenario is then run once per case, with the payload planted in a field
    somebody else writes. Two hundred injection attempts is one block, it is
    reproducible from the seed, and it runs unchanged against a regenerated
    agent — which hand-written cases do not, because they encode the phrasing
    whoever wrote them happened to think of.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = "injection"
    source: str = "templates"
    """Where the cases come from (0.11.0): `templates`, the default and every
    scenario's before it; `pyrit`, PyRIT's deterministic converters over seed
    injections; `agentdojo`, AgentDojo's attack templates and injection-task
    goals. A source other than `templates` also states each case's **goal** in
    this world's terms and judges it by state (`agenttwin.attacks`)."""
    seed: int = 1
    count: int = 10
    into: str
    """`entity.field` — where the payload is planted. Must be a field the
    specification marks `untrusted`, because planting an instruction in a field
    the *system* writes tests a threat that does not exist."""
    key: str
    """The row to plant it in."""


class ModelTurnFile(BaseModel):
    """One scripted answer from the model: words, tool calls, or both.

    **Why the script is in the file.** It used to be Python in the reference's
    test suite, built from that agent's own response types, so only that agent
    could be driven by it. Declared here, the provider twin serves it over the
    OpenAI-compatible wire, and any implementation that talks to a model through
    that wire is driven by the same answers.

        model:
          - calls: [{get_order: {id: AB-10003}}]
          - says: That order was delivered.
            times: 3

    Answers are served in order, one per model call, whatever the request said —
    the script is the model's side of the conversation, not a policy. A call
    beyond the last answer is an **overrun**: the implementation reached the
    model more often than the scenario's author expected, and the runner says so.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    says: str = ""
    calls: tuple[dict[str, dict[str, Any]], ...] = ()
    """Each `{operation: arguments}`, one key per entry, in the order the model
    asks for them."""
    times: int = Field(default=1, ge=1)
    """How many consecutive model calls get this same answer."""

    @model_validator(mode="after")
    def _says_or_calls(self) -> ModelTurnFile:
        if not self.says and not self.calls:
            raise ValueError("a model turn says something, calls something, or both")
        for call in self.calls:
            if len(call) != 1:
                raise ValueError(f"a call names exactly one operation, got {sorted(call)}")
        return self


class ScenarioEventFile(CalendarFile):
    """A calendar entry a scenario plants **before its first turn** (0.9.0).

    The world's own calendar runs on a day's clock; a scenario has no day, only
    turns. So a scenario's entry fires at t0, after the world is started and
    before anybody speaks, and `at` may only say so (`0`, `0s`, `0h`, or
    omitted). The point is the `record_only` entry: the record says one thing,
    the world's truth another, and the scenario asks whether the agent repeats
    the record as fact. The lab found exactly that (the carrier marks an
    undelivered parcel delivered and the agent states the scan); this is how
    the incident becomes a scenario a fix must turn green.

        calendar:
          - id: carrier-marks-it-delivered
            entity: order
            where: [{field: id, equals: [AB-10001]}]
            sets: {status: delivered}
            record_only: true
            by: carrier

    Same vocabulary as the world file's `calendar`, and the same load-time
    checks (entity owned, fields declared, enum values legal).
    """

    at: str = "0s"

    @model_validator(mode="after")
    def _at_t0(self) -> ScenarioEventFile:
        if _seconds(self.at) != 0:
            raise ValueError(
                f"calendar entry {self.id!r} is at {self.at!r}: a scenario's calendar fires "
                "at t0, before the first turn — a later moment belongs in a world's calendar"
            )
        return self


class ScenarioFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    forces: str = ""
    """The misbehaviour this scenario exists to catch, when it needs the model to
    commit it: *a model that promises to check and calls nothing*. Scripted, the
    model does it and the guard is proven. Against a live model that does not do
    it the guard never fires, and a check that waits for the guard reads as a
    failure it is not. A runner scoring live pass rates reports such a scenario as
    a guard the model did not need, not as a regression (reference-agent T-050)."""

    apiVersion: Literal["awd-scenario/v0"]
    scenario: str
    world: str
    """Path to the world, relative to this file."""
    as_: str = Field(alias="as")
    """The customer this run acts as — a record the world declares, never an
    identity or a scope, which are the binding's business."""
    objective: str = ""
    concern: Concern | None = None
    """Which kind of quality this scenario is about — the family-wide axis (Spec
    Charter §3): the nine characteristics of ISO/IEC 25010:2023 plus `cost`.

    Optional, and the only tag in this format that is. A scenario usually
    exercises several statements at once and inherits their concerns, so
    declaring one here says *this scenario exists for* — which is worth saying
    when it is not obvious from what it discharges, and noise when it is."""
    discharges: tuple[str, ...] = ()
    max_turns: int = 6
    step_seconds: int = 3600
    """How much time passes per turn, for the offstage humans. "The reviewer took
    an hour" is a property of the scenario, not of how slow the machine was.

    **Set it below the shortest window you want a person to beat.** The default
    is an hour, and an escalation that lapses in thirty minutes is gone before a
    colleague looks — so a scenario with a desk must state it, and loading one
    that does not is refused."""
    step_days: int = 0
    """How much time passes **in the world** per turn.

    Separate from `step_seconds` because they answer to different clocks: the
    harness's expiry windows are minutes and hours, while a return window is
    days, and a scenario that needed both would otherwise have to choose. Zero
    means the world is frozen, which is every scenario that is not about time."""
    actor: ActorFile = ActorFile()
    approver: ApproverFile | None = None
    desk: DeskFile | None = None
    perturbations: tuple[PerturbationFile, ...] = ()
    generate: GenerateFile | None = None
    model: tuple[ModelTurnFile, ...] = ()
    """What the model answers, in order, when the run is scripted. **Empty means
    the model must not be called at all** — a scenario answered by a
    deterministic route proves that from outside, because any model call is an
    overrun. Ignored by a live run, where a real model answers."""
    calendar: tuple[ScenarioEventFile, ...] = ()
    """Entries fired at t0, in order, before the first turn (0.9.0). With
    `record_only`, the record lies and the truth stays: every check that judges
    a reply reads `Live.truth()`, so repeating the lie fails the scenario."""
    expect: tuple[Check, ...] = ()

    def events(self) -> tuple[CalendarEvent, ...]:
        """The scenario's calendar as the world's `CalendarEvent`s, for `fire`."""
        return tuple(_event(entry) for entry in self.calendar)

    def scripted_answers(self) -> tuple[ModelTurnFile, ...]:
        """The script expanded by `times`: one entry per model call it answers."""
        return tuple(turn for turn in self.model for _ in range(turn.times))


def load_scenario(path: Path) -> ScenarioFile:
    """Read one scenario, or say why it cannot be read."""
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise InvalidScenario(f"{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise InvalidScenario(f"{path}: a scenario is a mapping")
    if raw.get("apiVersion") != API_VERSION:
        raise InvalidScenario(
            f"{path}: apiVersion is {raw.get('apiVersion')!r}, expected {API_VERSION!r}"
        )
    try:
        scenario = ScenarioFile.model_validate(raw)
    except Exception as exc:
        raise InvalidScenario(f"{path}: {exc}") from exc
    if not (path.parent / scenario.world).is_file():
        raise InvalidScenario(f"{path}: cites a world that is not there — {scenario.world}")
    if not scenario.expect:
        raise InvalidScenario(f"{path}: expects nothing, so it can never fail")
    if scenario.calendar:
        _check_scenario_calendar(path, scenario)
    if scenario.desk is not None and "step_seconds" not in scenario.model_fields_set:
        # An escalation's window is minutes and the default step an hour, so a
        # desk on the default tests a colleague arriving too late — and whether
        # that passes depends on when an implementation applies the lapse, which
        # the AOAS says is when the time passes (generation run 3, NOTES).
        raise InvalidScenario(
            f"{path}: has a desk and no step_seconds — say how long a turn takes, "
            "below the escalation window the desk is meant to beat, or above it "
            "to test the lapse"
        )
    return scenario


def _check_scenario_calendar(path: Path, scenario: ScenarioFile) -> None:
    """The world-file checks, against the world the scenario cites: an entry on
    an entity no system owns, over a field the entity lacks, or setting a value
    outside its enum is refused here, not mid-run."""
    ids = [entry.id for entry in scenario.calendar]
    if len(ids) != len(set(ids)):
        raise InvalidScenario(f"{path}: a calendar entry is declared twice: {ids}")
    try:
        world = load(path.parent / scenario.world)
        _check_calendar(world.model_copy(update={"calendar": scenario.events()}))
    except InvalidWorld as exc:
        raise InvalidScenario(f"{path}: {exc}") from exc
    clash = set(ids) & {event.id for event in world.calendar}
    if clash:
        raise InvalidScenario(
            f"{path}: calendar entry {sorted(clash)} is already the world's — name it apart, "
            "or an incident's root could not say which one made the record lie"
        )


__all__ = [
    "API_VERSION",
    "ActorFile",
    "InvalidScenario",
    "ModelTurnFile",
    "ScenarioEventFile",
    "ScenarioFile",
    "load_scenario",
]
