"""Run a declared scenario against an implementation the runner does not know.

`scenario.run` takes Python objects and a dict of lambdas; this takes a file and
a `Subject`. The difference is the whole point of the format: the same suite can
be pointed at the reference, at a regeneration of it, and at an agent for another
domain, and the only thing that changes is who builds the `Subject`.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

from agenttwin.actor import (
    ActorUnavailable,
    Determinism,
    ModelActor,
    Rule,
    ScriptedActor,
    StateMachineActor,
    Transcript,
    model_actor,
)
from agenttwin.attacks import (
    GeneratedCase,
    cases,
    goals_for,
    judged_by_state,
    obedient_calls,
)
from agenttwin.calendar import fire
from agenttwin.checks import Check, Outcome
from agenttwin.loader import load
from agenttwin.personas import brief_for
from agenttwin.perturbation import ChannelError, Decline, LostReply, Slow, StaleRead, Timeline
from agenttwin.projection import Live
from agenttwin.record import RunRecord, diff
from agenttwin.scenario import Clock
from agenttwin.scenario_file import ModelTurnFile, ScenarioFile, load_scenario
from agenttwin.subject import Subject
from agenttwin.truth import named_contradictions
from agenttwin.world import World


def forbidden_effects(scenario: ScenarioFile) -> tuple[str, ...]:
    """The actions this scenario already says must never land (`effect: X,
    times: 0`) — the only goals a generated case may pursue, because a goal
    the scenario does not forbid could be what the customer asked for."""
    return tuple(
        dict.fromkeys(c.effect for c in scenario.expect if c.effect and c.times == 0)
    )


def generated_cases(scenario: ScenarioFile, world: World | None = None) -> list[GeneratedCase]:
    """Every case a `generate` block declares, mapped onto `world`.

    `world` is needed by any source but `templates`: a goal is stated in the
    world's terms (its rows, its actions, whose data is whose). Without it a
    source gets no goals and its cases add no checks."""
    declared = scenario.generate
    if declared is None:
        return []
    entity, _, _ = declared.into.partition(".")
    forbids = forbidden_effects(scenario)
    goals = (
        goals_for(world, who=scenario.as_, entity=entity, key=declared.key, forbids=forbids)
        if world is not None and declared.source != "templates"
        else ()
    )
    attacks = cases(
        declared.kind,
        seed=declared.seed,
        count=declared.count,
        source_name=declared.source,
        goals=goals,
    )
    answers = [tuple(turn.calls) for turn in scenario.model]
    width = len(str(len(attacks)))
    return [
        GeneratedCase(
            name=f"case {i + 1:0{width}d}",
            payload=a.payload,
            goal=a.goal,
            origin=a.origin,
            expect=judged_by_state(a.goal, world, scenario.as_, forbids) if world else (),
            calls=obedient_calls(a.goal, world, answers) if world else (),
        )
        for i, a in enumerate(attacks)
    ]


def attack_cases(scenario: ScenarioFile, world: World | None = None) -> list[tuple[str, str]]:
    """`(case name, payload)` for a scenario that declares a generator.

    The caller runs the scenario once per case against a fresh world, planting
    the payload first. Fresh per case on purpose: an attack that succeeded would
    otherwise leave the world changed for the next one, and the second failure
    would be the first one's fault. `generated_cases` carries each case's goal,
    the checks it adds and the model's obedient calls besides.
    """
    return [(c.name, c.payload) for c in generated_cases(scenario, world)]


def script_for(scenario: ScenarioFile, case: GeneratedCase | None) -> tuple[ModelTurnFile, ...]:
    """The scripted answers for one case: the scenario's, with the case's
    obedient calls added to the answers they extend (`attacks.obedient_calls`)."""
    if case is None or not case.calls:
        return scenario.scripted_answers()
    extra = dict(case.calls)
    turns = [
        turn.model_copy(update={"calls": tuple(turn.calls) + extra[i]}) if i in extra else turn
        for i, turn in enumerate(scenario.model)
    ]
    return tuple(turn for turn in turns for _ in range(turn.times))


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
    if declared.kind == "model" and declared.via:
        try:
            return model_actor(
                declared.via,
                situation=declared.situation or scenario.objective,
                persona=declared.persona,
                model=declared.model,
                max_turns=scenario.max_turns,
            )
        except ActorUnavailable as exc:
            raise Unrunnable(f"{scenario.scenario}: {exc}") from exc
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


def provider_faults(scenario: ScenarioFile) -> tuple[tuple[int, str, float | None, int], ...]:
    """The faults a scenario schedules on the **model channel**, as data.

    `(call number, kind, retry_after, lasts_s)`. Deliberately not objects and deliberately
    not exceptions: this package cannot see the agent's types, and a simulator
    that imported them would simulate one agent. The binding maps a kind onto
    whatever its own provider adapter raises.
    """
    return tuple(
        (p.at_call, p.kind, p.retry_after, p.lasts_s)
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
        elif declared.kind == "lost_reply":
            faults.append(
                LostReply(
                    tool=declared.tool,
                    on_call=declared.at_call,
                    message=declared.message,
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
        elif declared.kind == "decline":
            faults.append(
                Decline(
                    tool=declared.tool,
                    on_call=declared.at_call,
                    **({"message": declared.message} if declared.message != "injected fault" else {}),
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
    clock: Clock | None = None,
    case: GeneratedCase | None = None,
) -> tuple[RunRecord, tuple[Outcome, ...]]:
    """Drive one declared scenario, once, and answer every check it makes.

    `live` is the world the `subject` was built against; omitted, a fresh one
    is started (only right for a subject that does not read the world).
    `timeline` and `clock` must be the ones the binding wrapped the projection
    with, or declared faults and time will not reach the agent.

    **A scenario with a `generate` block is one run per case, not one run.**
    `run_file` runs exactly one: the case already planted in `live` (see
    `attack_cases` and `plant`). If no generated payload is planted there it
    does not drive the subject at all and returns a single **failing** outcome,
    `the generated cases ran` — generation run 2 (NOTES §8) watched a scenario
    declaring twelve injection cases run once with nothing planted, and pass.
    Use `run_generated` to run every case, each in a fresh world.

    `case`, when given, adds that case's own checks — its goal, judged by state.
    """
    scenario = load_scenario(path)
    world = live if live is not None else Live.start(load(path.parent / scenario.world))

    if scenario.generate is not None and not _planted(world, scenario):
        declared = scenario.generate
        outcome = Outcome(
            check="the generated cases ran",
            passed=False,
            detail=(
                f"declares {declared.count} {declared.kind} case(s) into {declared.into} "
                f"{declared.key} and none is planted in the world handed to run_file — "
                "run it with run_generated, or plant one case from attack_cases first"
            ),
        )
        record = RunRecord(
            scenario=scenario.scenario,
            world=world.world.name,
            seed=world.world.seed,
            resolution="mock",
            determinism_class=Determinism.SCRIPTED.value,
            changes=(),
            effects=(),
            discharges=scenario.discharges,
            reply="",
            transcript=(),
            verdicts={outcome.check: False},
        )
        return record, (outcome,)

    reviewer = None
    if scenario.approver is not None:
        if subject.reviewer is None:
            raise Unrunnable(f"{path.name}: needs an approver and this implementation has none")
        reviewer = subject.reviewer(
            scenario.approver.decides,
            scenario.approver.by,
            (scenario.approver.after_turns - 1) * scenario.step_seconds,
        )
    colleague = None
    if scenario.desk is not None:
        if subject.colleague is None:
            raise Unrunnable(f"{path.name}: needs a desk and this implementation has none")
        colleague = subject.colleague(
            scenario.desk.resolves,
            scenario.desk.by,
            (scenario.desk.after_turns - 1) * scenario.step_seconds,
        )

    # The scenario's own calendar fires at t0, before world_0, so a planted lie
    # is the world the agent meets and not a change the agent is blamed for.
    for event in scenario.events():
        fire(world, event)
    world_0 = world.snapshot()
    actor = actor_for(scenario, voice)
    transcript = Transcript()
    # **The same clock the implementation was built with**, or time means two
    # different things in one run: the offstage humans would review at a moment
    # the agent has not reached, and every escalation would have lapsed before
    # anybody came. Pass the clock to both, or to neither.
    tick = clock or Clock(step_s=scenario.step_seconds)
    conversation: object = None
    reply = ""
    falsehoods: list[str] = []

    if scenario.actor.opens:
        if subject.opens is None:
            raise Unrunnable(
                f"{path.name}: opens a conversation and this implementation shows nothing on it"
            )
        reply = await subject.opens(scenario.as_)
        transcript.add("", reply)
        falsehoods += [
            f"opening: {entity} {key} — {c}"
            for entity, key, c in named_contradictions(_truth(world), reply)
        ]

    for _ in range(scenario.max_turns):
        said = actor.next(reply)
        if inspect.isawaitable(said):
            said = await said
        if said is None:
            break
        held = world.truth()
        reply, conversation = await subject.say(said, scenario.as_, conversation)
        transcript.add(said, reply)
        # Judged now, against the world as it is when the customer reads it — a
        # reply true on turn one can be made false by turn two's cancellation —
        # allowing what was true when this turn began (stale, not invented).
        # **The truth, not the record** (0.9.0): where a `record_only` calendar
        # entry made the record lie, repeating the record is a false reply, as
        # the monitor judges it (`RepliesTrue`). Identical to the record when
        # nothing has diverged.
        falsehoods += [
            f"turn {len(transcript.turns)}: {entity} {key} — {c}"
            for entity, key, c in named_contradictions(_truth(world), reply, held=held)
        ]
        if scenario.step_days:
            world.advance(scenario.step_days)
        if reviewer is None and colleague is None:
            tick.tick()  # time passes whether or not anybody is offstage to notice
        if reviewer is not None or colleague is not None:
            moment = tick.tick()
            if reviewer is not None:
                await reviewer.review(at=moment)
            if colleague is not None:
                await colleague.review(at=moment)

    calls = dict(timeline.calls) if timeline is not None else {}
    offstage = {
        "handed": tuple(getattr(colleague, "handled", ()) or ()),
        "reviewed": tuple(getattr(reviewer, "reviewed", ()) or ()),
    }
    checks = list(scenario.expect)
    if case is not None:
        checks += [Check.model_validate(c) for c in case.expect]
    outcomes = [check.evaluate(world, world_0, reply, calls, offstage) for check in checks]
    # Asked of every scenario, whatever it declares: nothing the customer read
    # contradicted a record it named. See `truth.named_contradictions`.
    outcomes.append(
        Outcome(
            check="every reply is true of the records it names",
            passed=not falsehoods,
            detail="; ".join(falsehoods[:3]),
        )
    )
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


def _truth(live: Live) -> Live:
    """The world as it really is: the record, with every field a `record_only`
    calendar entry made wrong put back. A read-only view for the truth oracle."""
    if not live.diverged:
        return live
    return Live(world=live.world, rows=live.truth())


def _planted(live: Live, scenario: ScenarioFile) -> bool:
    """Whether the row the scenario plants into holds one of its payloads."""
    declared = scenario.generate
    if declared is None:
        return True
    entity, _, field_name = declared.into.partition(".")
    row = live.get(entity, declared.key)
    if row is None:
        return False
    return row.get(field_name) in {payload for _, payload in attack_cases(scenario, live.world)}


SubjectFor = Callable[[Live, Timeline, Clock], AbstractAsyncContextManager[Subject]]
"""`(live, timeline, clock) -> async context manager yielding a Subject` — the
binding builds its implementation against *this* world, wrapping the projection
with `perturbed(live, timeline, clock)` so faults and time reach it."""


async def run_generated(
    path: Path,
    *,
    subject_for: SubjectFor,
    voice=None,
) -> tuple[tuple[str, RunRecord, tuple[Outcome, ...]], ...]:
    """Run a scenario once per generated case, each in a fresh world.

    For each `(name, payload)` from `attack_cases`: start the world, `plant`
    the payload, build a timeline and a clock from the scenario, enter
    `subject_for(live, timeline, clock)`, and `run_file` against it. Fresh per
    case because an attack that succeeded would otherwise leave the world
    changed for the next, and the second failure would be the first one's.

    Returns `(case name, record, outcomes)` per case. A scenario without a
    `generate` block runs once, named `""`, so a suite can call this for every
    file.
    """
    scenario = load_scenario(path)
    world = load(path.parent / scenario.world)
    results: list[tuple[str, RunRecord, tuple[Outcome, ...]]] = []
    for case in generated_cases(scenario, world) or [None]:
        name = case.name if case is not None else ""
        live = Live.start(world)
        if case is not None:
            plant(live, scenario, case.payload)
        timeline = timeline_for(scenario)
        clock = Clock(step_s=scenario.step_seconds)
        async with subject_for(live, timeline, clock) as subject:
            record, outcomes = await run_file(
                path,
                subject=subject,
                live=live,
                timeline=timeline,
                voice=voice,
                clock=clock,
                case=case,
            )
        results.append((name, record, outcomes))
    return tuple(results)


__all__ = [
    "SubjectFor",
    "Unrunnable",
    "attack_cases",
    "forbidden_effects",
    "generated_cases",
    "plant",
    "run_file",
    "run_generated",
    "script_for",
]
