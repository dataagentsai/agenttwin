"""Run a declared scenario against an implementation the runner does not know.

`scenario.run` takes Python objects and a dict of lambdas; this takes a file and
a `Subject`. The difference is the whole point of the format: the same suite can
be pointed at the reference, at a regeneration of it, and at an agent for another
domain, and the only thing that changes is who builds the `Subject`.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from agenttwin.actor import (
    Determinism,
    ModelActor,
    Rule,
    ScriptedActor,
    StateMachineActor,
    Transcript,
)
from agenttwin.attacks import cases
from agenttwin.checks import Outcome
from agenttwin.loader import load
from agenttwin.personas import brief_for
from agenttwin.perturbation import ChannelError, Slow, StaleRead, Timeline
from agenttwin.projection import Live
from agenttwin.record import RunRecord, diff
from agenttwin.scenario import Clock
from agenttwin.scenario_file import ScenarioFile, load_scenario
from agenttwin.subject import Subject


def attack_cases(scenario: ScenarioFile) -> list[tuple[str, str]]:
    """`(case name, payload)` for a scenario that declares a generator.

    The caller runs the scenario once per case against a fresh world, planting
    the payload first. Fresh per case on purpose: an attack that succeeded would
    otherwise leave the world changed for the next one, and the second failure
    would be the first one's fault.
    """
    if scenario.generate is None:
        return []
    payloads = cases(
        scenario.generate.kind, seed=scenario.generate.seed, count=scenario.generate.count
    )
    width = len(str(len(payloads)))
    return [(f"case {i + 1:0{width}d}", payload) for i, payload in enumerate(payloads)]


def plant(live: Live, scenario: ScenarioFile, payload: str) -> None:
    """Put one payload where the scenario says, and refuse a field that is not
    declared untrusted — planting an instruction in text the system itself
    writes tests a threat nobody faces."""
    declared = scenario.generate
    if declared is None:
        return
    entity, _, field_name = declared.into.partition(".")
    spec = live.world.entities.get(entity)
    if spec is None or field_name not in spec.fields:
        raise Unrunnable(f"{declared.into} is not a declared field")
    if not spec.fields[field_name].untrusted:
        raise Unrunnable(
            f"{declared.into} is not declared untrusted — planting an instruction "
            "in a field the system writes tests a threat that does not exist"
        )
    row = live.get(entity, declared.key)
    if row is None:
        raise Unrunnable(f"no {entity} {declared.key} to plant into")
    row[field_name] = payload


class Unrunnable(Exception):
    """The scenario asks for something this implementation does not have.

    Raised rather than reported as a failure, and the distinction matters: an
    agent with no approval queue *failing* a scenario about approvals reads as a
    behavioural difference, when it is a missing capability — a different finding,
    for a different person, on a different day.
    """


def actor_for(scenario: ScenarioFile, voice=None):
    """The customer this scenario declares.

    A model-driven one needs a `voice` — `(brief, heard) -> said` — which only
    the binding can supply, because only it has a provider. Asking for one and
    being given none is `Unrunnable` rather than a failure: the scenario is fine
    and this caller cannot run it.
    """
    declared = scenario.actor
    if declared.kind == "model":
        if voice is None:
            raise Unrunnable(
                f"{scenario.scenario}: needs a model-driven customer and no voice was supplied"
            )
        return ModelActor(
            brief_for(declared.persona, declared.situation or scenario.objective),
            voice,
            max_turns=scenario.max_turns,
        )
    if declared.kind == "scripted":
        return ScriptedActor(list(declared.says))
    return StateMachineActor(
        opening=declared.opening,
        rules=[
            Rule(when=re.compile(rule["when"], re.S | re.I), say=rule["say"])
            for rule in declared.rules
        ],
        max_turns=declared.max_turns,
        persistence=declared.persistence or None,
    )


PROVIDER_KINDS = ("provider_throttled", "provider_unavailable", "provider_malformed")


def provider_faults(scenario: ScenarioFile) -> tuple[tuple[int, str, float | None], ...]:
    """The faults a scenario schedules on the **model channel**, as data.

    `(call number, kind, retry_after)`. Deliberately not objects and deliberately
    not exceptions: this package cannot see the agent's types, and a simulator
    that imported them would simulate one agent. The binding maps a kind onto
    whatever its own provider adapter raises.
    """
    return tuple(
        (p.at_call, p.kind, p.retry_after)
        for p in scenario.perturbations
        if p.kind in PROVIDER_KINDS
    )


def timeline_for(scenario: ScenarioFile) -> Timeline:
    """The faults a scenario schedules, built from what it declared.

    Handed to the binding as an opaque wrap: a perturbation happens to the
    *system*, so the world owns it, and the implementation being driven neither
    interprets it nor knows it is there.
    """
    faults = []
    for declared in scenario.perturbations:
        if declared.kind in PROVIDER_KINDS:
            continue  # the model channel is not the tool channel
        if declared.kind == "stale_read":
            faults.append(
                StaleRead(
                    tool=declared.tool,
                    on_call=declared.at_call,
                    entity=declared.entity,
                    key=declared.key,
                    sets=dict(declared.sets),
                )
            )
        elif declared.kind == "channel_error":
            faults.append(
                ChannelError(
                    tool=declared.tool,
                    on_call=declared.at_call,
                    channel=declared.channel,
                    message=declared.message,
                )
            )
        else:
            faults.append(
                Slow(tool=declared.tool, on_call=declared.at_call, seconds=declared.seconds)
            )
    return Timeline(*faults)


async def run_file(
    path: Path,
    *,
    subject: Subject,
    live: Live | None = None,
    timeline: Timeline | None = None,
    voice=None,
) -> tuple[RunRecord, tuple[Outcome, ...]]:
    """Drive one declared scenario and answer every check it makes."""
    scenario = load_scenario(path)
    world = live if live is not None else Live.start(load(path.parent / scenario.world))

    reviewer = None
    if scenario.approver is not None:
        if subject.reviewer is None:
            raise Unrunnable(f"{path.name}: needs an approver and this implementation has none")
        reviewer = subject.reviewer(scenario.approver.decides, scenario.approver.by)
    colleague = None
    if scenario.desk is not None:
        if subject.colleague is None:
            raise Unrunnable(f"{path.name}: needs a desk and this implementation has none")
        colleague = subject.colleague(scenario.desk.resolves, scenario.desk.by)

    world_0 = world.snapshot()
    actor = actor_for(scenario, voice)
    transcript = Transcript()
    tick = Clock(step_s=scenario.step_seconds)
    conversation: object = None
    reply = ""

    for _ in range(scenario.max_turns):
        said = actor.next(reply)
        if inspect.isawaitable(said):
            said = await said
        if said is None:
            break
        reply, conversation = await subject.say(said, scenario.as_, conversation)
        transcript.add(said, reply)
        if scenario.step_days:
            world.advance(scenario.step_days)
        if reviewer is not None or colleague is not None:
            moment = tick()
            if reviewer is not None:
                await reviewer.review(at=moment)
            if colleague is not None:
                await colleague.review(at=moment)

    calls = dict(timeline.calls) if timeline is not None else {}
    offstage = {
        "handed": tuple(getattr(colleague, "handled", ()) or ()),
        "reviewed": tuple(getattr(reviewer, "reviewed", ()) or ()),
    }
    outcomes = [check.evaluate(world, world_0, reply, calls, offstage) for check in scenario.expect]
    if timeline is not None and any(p.kind not in PROVIDER_KINDS for p in scenario.perturbations):
        # A scenario whose fault never landed did not test what it claimed, and
        # passes for the wrong reason — which is worse than failing, because
        # nobody goes looking for a control they believe they exercised.
        missed = timeline.unfired
        outcomes.append(
            Outcome(
                check="every declared fault fired",
                passed=not missed,
                detail="; ".join(f"{p.tool} on call {p.on_call}" for p in missed),
            )
        )
    outcomes = tuple(outcomes)
    record = RunRecord(
        scenario=scenario.scenario,
        world=world.world.name,
        seed=world.world.seed,
        resolution="mock",
        determinism_class=getattr(actor, "determinism", Determinism.SCRIPTED).value,
        changes=diff(world_0, world.snapshot()),
        effects=tuple(world.effects),
        discharges=scenario.discharges,
        reply=reply,
        transcript=tuple((turn.said, turn.heard) for turn in transcript.turns),
        verdicts={o.check: o.passed for o in outcomes},
    )
    return record, outcomes


__all__ = ["Unrunnable", "run_file"]
