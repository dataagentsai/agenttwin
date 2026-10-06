"""Making the world behave badly on purpose.

Tier 2. Everything so far has tested a world that answers correctly. This is the
half that finds what the well-behaved world could not.

Fault injection is mature — Toxiproxy, Chaos Mesh, Hypothesis — and has never
been applied at an agent's tool boundary. That is what this is: the same idea,
one layer up, where the failures are about *decisions* rather than packets.

## The perturbations that matter here

**Stale read.** The agent reads a state, decides, and acts — and the world moved
in between. This is the check-then-act race, it is recorded as **G2** against the
catalog because no AAC obligation covers it, and it is the one most likely to
find something.

**Both MCP error channels.** A protocol error and an execution error are
different objects with different recovery paths. An agent that handles one and
not the other looks healthy until production.

**Lost reply.** The call ran, the world moved, and the answer never came back.
The other three all leave the caller's record and the world agreeing with each
other; this is the one that pulls them apart, and it is the only failure where
retrying is dangerous rather than free. Idempotency exists for exactly this, and
a suite without it can only test that the mechanism is *present*, never that it
*works*.

**Latency.** Not to measure speed — to open the window a stale read needs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agenttwin.projection import Live


class IncoherentPerturbation(Exception):
    """A scheduled fault would leave the world in a state it calls impossible."""


@dataclass
class Perturbation:
    """One scheduled misbehaviour, fired on the nth call to a named tool.

    Counting from one and firing on a *specific* call rather than randomly: a
    fault that lands somewhere different on each run produces a failure nobody
    can reproduce, which is the opposite of the point.
    """

    tool: str
    on_call: int = 1
    fired: bool = False

    def should_fire(self, tool: str, call_number: int) -> bool:
        return not self.fired and tool == self.tool and call_number == self.on_call


@dataclass
class StaleRead(Perturbation):
    """Change the world *after* a read has been answered.

    The classic time-of-check to time-of-use window, staged deliberately. The
    interesting question is not whether the tool server re-checks — it does —
    but whether the agent reports the outcome it was *told* or the one it
    *expected*.
    """

    entity: str = "order"
    key: str = ""
    sets: dict = field(default_factory=dict)

    def apply(self, live: Live) -> None:
        row = live.get(self.entity, self.key)
        if row is not None:
            moved = {**row, **self.sets}
            # A fault that lands the world in a state it declared impossible is
            # a bug in the scenario, not a finding about the agent — and it is
            # the kind that reads as a pass. Same invariants the loader and the
            # generator use, enforced at the third place the world can move.
            broken = live.world.entities[self.entity].violations(moved)
            if broken:
                raise IncoherentPerturbation(
                    f"stale read on {self.entity} {self.key} would set {self.sets} "
                    f"and break {broken[0].name!r}: {broken[0].because}"
                )
            row.update(self.sets)
        self.fired = True


@dataclass
class ChannelError(Perturbation):
    """Fail a call on one of MCP's two channels, deliberately named.

    `protocol` raises out of the tool and surfaces as a JSON-RPC error;
    `execution` returns a normal result carrying `isError`. The model is expected
    to recover from the second and rarely can from the first.
    """

    channel: str = "execution"
    message: str = "injected fault"

    def apply(self, live: Live) -> None:
        self.fired = True


@dataclass
class Decline(Perturbation):
    """Refuse a write for good: the same answer on this call and every one after.

    What a closed card looks like to a refund (P-REFUND-DECLINED, T-095). The
    far end is reachable and answers promptly; it will never say yes. Unlike
    `ChannelError`, which fails one call and may clear, a retry here buys the
    same refusal — so a caller that retries five times and tells the customer to
    try tomorrow is visible as exactly that.

    The answer is a result, not an error, and it names its kind as a field
    (`kind: declined`), so the caller never has to infer permanent from the
    wording (AHC-0043). Nothing in the world moves.
    """

    message: str = "declined by the payment provider: the original payment method can no longer receive funds"

    def should_fire(self, tool: str, call_number: int) -> bool:
        return tool == self.tool and call_number >= self.on_call

    def apply(self, live: Live) -> None:
        self.fired = True


@dataclass
class LostReply(Perturbation):
    """Run the call, then lose the answer on the way back.

    The one shape the other three cannot express. `ChannelError` fails *instead
    of* acting, so the world never moved and a retry is free. `StaleRead` acts
    and answers, then moves the world. This acts, changes the world, and then
    the caller is told nothing — which is the only case where a retry is
    dangerous, because the caller's own record says the effect never happened.

    It is the failure that idempotency exists for, and the one no ledger on the
    calling side can catch: the harness cannot record what it was never told.
    Only the far end, recognising the key it was given, can tell a retry from a
    second request. A scenario that stages this is therefore asking the question
    that matters — not *does the agent retry*, but *does the retry charge them
    twice*.
    """

    message: str = "the reply was lost"

    def apply(self, live: Live) -> None:
        self.fired = True


@dataclass
class Slow(Perturbation):
    """Declare that a call took time. Not a latency measurement — a window opener.

    Given a clock it moves that clock, and it must be, or it opens nothing. The
    freshness window an agent measures a belief against is read off the clock the
    *scenario* declares; `asyncio.sleep` moves the wall clock instead, so for as
    long as this only slept, a read and the action taken on it were always
    zero seconds apart however long the suite waited.
    """

    seconds: float = 0.05

    def apply(self, live: Live) -> None:
        self.fired = True


class Timeline:
    """The perturbations a scenario schedules, and what actually fired.

    `unfired` is reported rather than ignored. A scenario whose fault never
    landed did not test what it claimed, and passes for the wrong reason — which
    is worse than failing.
    """

    def __init__(self, *perturbations: Perturbation) -> None:
        self.perturbations = list(perturbations)
        self.calls: dict[str, int] = {}
        self.log: list[str] = []

    def next_call(self, tool: str) -> int:
        self.calls[tool] = self.calls.get(tool, 0) + 1
        return self.calls[tool]

    def due(self, tool: str, call_number: int) -> list[Perturbation]:
        return [p for p in self.perturbations if p.should_fire(tool, call_number)]

    @property
    def unfired(self) -> tuple[Perturbation, ...]:
        return tuple(p for p in self.perturbations if not p.fired)


def perturbed(live: Live, timeline: Timeline, clock: Any = None) -> Callable:
    """Wrap the projection's dispatch so the timeline can intervene.

    Applied as a decorator around each generated handler by `project_perturbed`
    below, rather than inside `projection.py`, so a world with no timeline pays
    nothing and the well-behaved path stays the simple one.
    """

    def wrap(tool: str, handler: Callable) -> Callable:
        async def wrapped(**arguments):
            number = timeline.next_call(tool)
            for perturbation in timeline.due(tool, number):
                if isinstance(perturbation, Slow):
                    perturbation.apply(live)
                    timeline.log.append(f"slow {tool} call {number}: {perturbation.seconds}s")
                    if clock is not None:
                        # Declared time, not elapsed time. Sleeping costs the
                        # suite real seconds and still does not move the clock
                        # the agent reads — which is why this "window opener"
                        # had never opened one.
                        clock.advance(perturbation.seconds)
                    else:
                        await asyncio.sleep(perturbation.seconds)
                elif isinstance(perturbation, ChannelError):
                    perturbation.apply(live)
                    timeline.log.append(f"{perturbation.channel} error on {tool} call {number}")
                    if perturbation.channel == "protocol":
                        raise RuntimeError(perturbation.message)
                    # `kind` says what a caller may do about it, as a field, so
                    # nobody infers it from the message (AHC-0043, T-095).
                    return {
                        "allowed": False,
                        "reason": perturbation.message,
                        "kind": "transient",
                        "injected": True,
                    }
                elif isinstance(perturbation, Decline):
                    perturbation.apply(live)
                    timeline.log.append(f"declined {tool} call {number}")
                    return {
                        "allowed": False,
                        "reason": perturbation.message,
                        "kind": "declined",
                        "injected": True,
                    }

            result = await handler(**arguments)

            # Stale reads fire *after* the answer is produced, which is the whole
            # point: the caller has already been told something that is no longer
            # true.
            for perturbation in timeline.due(tool, number):
                if isinstance(perturbation, StaleRead):
                    perturbation.apply(live)
                    timeline.log.append(f"stale read after {tool} call {number}")
                elif isinstance(perturbation, LostReply):
                    # After the handler, so the effect is already in the world,
                    # and raising rather than returning, so the caller learns
                    # nothing at all. Returning an error here would be a
                    # different and much kinder fault: the caller would at least
                    # know the call was attempted.
                    perturbation.apply(live)
                    timeline.log.append(f"lost reply after {tool} call {number}")
                    raise RuntimeError(perturbation.message)
            return result

        wrapped.__name__ = handler.__name__
        wrapped.__doc__ = handler.__doc__
        wrapped.__signature__ = handler.__signature__  # type: ignore[attr-defined]
        wrapped.__annotations__ = handler.__annotations__
        return wrapped

    return wrap


__all__ = [
    "ChannelError",
    "Decline",
    "LostReply",
    "Perturbation",
    "Slow",
    "StaleRead",
    "Timeline",
    "perturbed",
]
