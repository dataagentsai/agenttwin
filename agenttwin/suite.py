"""Run a declared scenario against an implementation the runner does not know.

`scenario.run` takes Python objects and a dict of lambdas; this takes a file and
a `Subject`. The difference is the whole point of the format: the same suite can
be pointed at the reference, at a regeneration of it, and at an agent for another
domain, and the only thing that changes is who builds the `Subject`.
"""

from __future__ import annotations

import re
from pathlib import Path

from agenttwin.actor import Determinism, Rule, ScriptedActor, StateMachineActor, Transcript
from agenttwin.checks import Outcome
from agenttwin.loader import load
from agenttwin.projection import Live
from agenttwin.record import RunRecord, diff
from agenttwin.scenario import Clock
from agenttwin.scenario_file import ScenarioFile, load_scenario
from agenttwin.subject import Subject


class Unrunnable(Exception):
    """The scenario asks for something this implementation does not have.

    Raised rather than reported as a failure, and the distinction matters: an
    agent with no approval queue *failing* a scenario about approvals reads as a
    behavioural difference, when it is a missing capability — a different finding,
    for a different person, on a different day.
    """


def actor_for(scenario: ScenarioFile):
    declared = scenario.actor
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


async def run_file(
    path: Path, *, subject: Subject, live: Live | None = None
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
    actor = actor_for(scenario)
    transcript = Transcript()
    tick = Clock(step_s=scenario.step_seconds)
    conversation: object = None
    reply = ""

    for _ in range(scenario.max_turns):
        said = actor.next(reply)
        if said is None:
            break
        reply, conversation = await subject.say(said, scenario.as_, conversation)
        transcript.add(said, reply)
        if reviewer is not None or colleague is not None:
            moment = tick()
            if reviewer is not None:
                await reviewer.review(at=moment)
            if colleague is not None:
                await colleague.review(at=moment)

    outcomes = tuple(check.evaluate(world, world_0, reply) for check in scenario.expect)
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
        verdicts={o.check: o.passed for o in outcomes},
    )
    return record, outcomes


__all__ = ["Unrunnable", "run_file"]
