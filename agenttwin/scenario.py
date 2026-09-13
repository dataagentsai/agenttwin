"""World + actor + objective + predicate. One scenario, one run record.

The unit an AAC obligation is discharged against, and the shape doc 26 declared:

    (world₀, scenario, agent) ⟶ (world₁, trace, verdict)

The predicates take the **live world**, not the transcript. That is the whole
reason this exists: a scenario asserts on what changed, and the reply is checked
only where the claim itself is the failure.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agenttwin.actor import Determinism, Transcript, weakest
from agenttwin.projection import Live
from agenttwin.record import RunRecord, diff

Predicate = Callable[[Live, Transcript], bool]


class Clock:
    """Wall time that advances in declared steps rather than by elapsing.

    Starts from the real clock, because the queue stamps approvals with it, and
    then moves by a fixed step **per turn**. So *"the reviewer took an hour"* is
    a property of the scenario and not of how slow the machine was — and a run
    that passes on a fast laptop passes in CI.

    **Reading it does not move it.** It used to advance on every call, which is
    unusable the moment the clock is shared: the agent reads the time several
    times a turn — minting an approval, checking a hold, stamping an escalation —
    and each read pushed the world further into the future, so an offstage
    reviewer arrived after windows that had expired while the agent was thinking
    (F-033). One `tick()` per turn moves it; everybody else asks what time it is.
    """

    def __init__(self, *, start: int | None = None, step_s: int = 3600) -> None:
        self.now = start if start is not None else int(time.time())
        self.step_s = step_s

    def __call__(self) -> int:
        """What time it is. Idempotent, because a reader is not an event."""
        return self.now

    def tick(self) -> int:
        """A turn has passed."""
        self.now += self.step_s
        return self.now

    def advance(self, seconds: float) -> int:
        """Time passed *inside* a turn, because a call took it.

        `tick` is a turn boundary; this is one slow call. The difference is
        invisible to most things and decisive to anything measuring how old a
        belief is — an agent that read a row, waited, and then acted on it holds
        a staler belief than one that did both at once, which is the whole
        subject of a freshness window.

        Without this there was no way to express it. A perturbation could sleep,
        but sleeping moves the wall clock and the agent is reading this one, so
        the window stayed shut however long the suite waited.
        """
        self.now += int(seconds)
        return self.now


@dataclass
class Scenario:
    name: str
    objective: str = ""
    max_turns: int = 6
    predicates: dict[str, Predicate] = field(default_factory=dict)
    discharges: tuple[str, ...] = ()


async def run(
    scenario: Scenario,
    *,
    live: Live,
    actor,
    agent,
    identity,
    approver=None,
    desk=None,
    clock: Callable[[], int] | None = None,
    resolution: str = "mock",
    config_fingerprint: str = "",
) -> RunRecord:
    """Drive actor and agent against each other until one of them stops.

    The turn budget belongs to the scenario as well as the actor: an actor that
    never stops is a scenario that never ends, and a suite that hangs is a suite
    nobody runs.

    **`approver` is a second actor and reviews between turns** — which is when a
    real reviewer acts: while the customer is still in the conversation, and
    without either party knowing what the other is doing. It never speaks to the
    agent; it acts on the queue out of band, and the agent finds out only when it
    next resumes.

    `clock` supplies the moment the reviewer decides at. It advances per turn, so
    "the reviewer took an hour" is a property of the scenario rather than of how
    long the test happened to take.
    """
    world_0 = live.snapshot()
    transcript = Transcript()
    conversation = None
    reply = ""
    tick = clock or Clock()

    for _ in range(scenario.max_turns):
        said = actor.next(reply)
        if said is None:
            break
        result, conversation = await agent.handle(
            said, identity=identity, conversation=conversation
        )
        reply = getattr(result, "reply", "") or getattr(result, "customer_message", "")
        transcript.add(said, reply)

        # One tick per turn, shared. Two offstage humans acting on the same turn
        # must experience the same moment, or a scenario's "an hour passed"
        # would mean two hours the moment a second reviewer joined it.
        if approver is not None or desk is not None:
            moment = tick.tick() if isinstance(tick, Clock) else tick()
            if approver is not None:
                await approver.review(at=moment)
            if desk is not None:
                await desk.review(at=moment)

    return RunRecord(
        scenario=scenario.name,
        world=live.world.name,
        seed=live.world.seed,
        resolution=resolution,  # type: ignore[arg-type]
        config_fingerprint=config_fingerprint,
        determinism_class=weakest(getattr(actor, "determinism", Determinism.SCRIPTED)).value,
        changes=diff(world_0, live.snapshot()),
        effects=tuple(live.effects),
        discharges=scenario.discharges,
        reply=reply,
        verdicts={
            name: predicate(live, transcript) for name, predicate in scenario.predicates.items()
        },
    )


__all__ = ["Clock", "Predicate", "Scenario", "run"]
