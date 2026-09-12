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

**What a scenario may not do** is name a tool, a scope, a model or an endpoint.
Those are the binding's, and a scenario that named one would run against exactly
one implementation, which is the thing being escaped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agenttwin.checks import Check

API_VERSION = "awd-scenario/v0"


class InvalidScenario(Exception):
    """Said at load, where it is cheap, rather than mid-run."""


class ActorFile(BaseModel):
    """Who is talking. `says` is a script; a rule set is a state machine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["scripted", "state_machine", "model"] = "scripted"
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
    """`channel_error` only. The model is expected to recover from an execution
    error and rarely can from a protocol one, so which is declared matters."""

    seconds: float = 0.05
    """`slow` only. A window opener, never a latency measurement."""

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
    seed: int = 1
    count: int = 10
    into: str
    """`entity.field` — where the payload is planted. Must be a field the
    specification marks `untrusted`, because planting an instruction in a field
    the *system* writes tests a threat that does not exist."""
    key: str
    """The row to plant it in."""


class ScenarioFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    apiVersion: Literal["awd-scenario/v0"]
    scenario: str
    world: str
    """Path to the world, relative to this file."""
    as_: str = Field(alias="as")
    """The customer this run acts as — a record the world declares, never an
    identity or a scope, which are the binding's business."""
    objective: str = ""
    discharges: tuple[str, ...] = ()
    max_turns: int = 6
    step_seconds: int = 3600
    """How much time passes per turn, for the offstage humans. "The reviewer took
    an hour" is a property of the scenario, not of how slow the machine was.

    **Set it below the shortest window you want a person to beat.** The default
    is an hour, and an escalation that lapses in thirty minutes is gone before a
    colleague looks — so a scenario with a desk and no `step_seconds` tests the
    desk arriving too late, which is a real case and rarely the intended one."""
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
    expect: tuple[Check, ...] = ()


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
    return scenario


__all__ = ["API_VERSION", "ActorFile", "InvalidScenario", "ScenarioFile", "load_scenario"]
