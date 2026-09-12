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
from pydantic import BaseModel, ConfigDict, Field

from agenttwin.checks import Check

API_VERSION = "awd-scenario/v0"


class InvalidScenario(Exception):
    """Said at load, where it is cheap, rather than mid-run."""


class ActorFile(BaseModel):
    """Who is talking. `says` is a script; a rule set is a state machine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["scripted", "state_machine"] = "scripted"
    says: tuple[str, ...] = ()
    opening: str = ""
    rules: tuple[dict[str, str], ...] = ()
    persistence: str = ""
    max_turns: int = 6


class ApproverFile(BaseModel):
    """The offstage reviewer. Acts on the queue between turns, never speaks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decides: Literal["grant", "refuse", "never"] = "grant"
    by: str = "ops-7"
    after_turns: int = 1


class DeskFile(BaseModel):
    """The offstage colleague. Resolves an escalation, or never comes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolves: Literal["handled", "never"] = "handled"
    by: str = "desk-1"
    after_turns: int = 1


class PerturbationFile(BaseModel):
    """Something going wrong that is nobody's fault, scheduled on a named call.

    On a *specific* call rather than randomly: a fault that lands somewhere
    different each run produces a failure nobody can reproduce.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["stale_read", "slow", "channel_error"]
    tool: str
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
    """How much time passes per turn. "The reviewer took an hour" is a property
    of the scenario, not of how slow the machine was."""
    actor: ActorFile = ActorFile()
    approver: ApproverFile | None = None
    desk: DeskFile | None = None
    perturbations: tuple[PerturbationFile, ...] = ()
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
