"""The agent contract — everything a scenario needs from an implementation.

Three callables and nothing else. A scenario cannot name a tool, a scope, a
model or a store, so it cannot be written against one implementation by accident;
and an implementation supplies this without the runner learning anything about
how it is built.

**The binding builds it, because wiring is the binding's business.** Which
provider, which stores, which scopes the projected world is gated with — all of
that happens inside `subject_for`, on the far side of this boundary, where a
regenerated agent will have made its own choices.

    async with implementation.subject_for(live, clock) as subject:
        record = await run_scenario(scenario, live=live, subject=subject)

What the runner is allowed to know: the customer said something and a reply came
back; a reviewer may act on a queue between turns; a colleague may pick up an
escalation. That is the whole of it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol


class Offstage(Protocol):
    """A human who acts between turns and never speaks to the agent."""

    async def review(self, *, at: int | None = None) -> Any: ...


@dataclass(frozen=True)
class Subject:
    """One implementation, mid-run, as a scenario sees it."""

    say: Callable[[str, str, Any], Awaitable[tuple[str, Any]]]
    """`(text, customer_id, conversation) -> (reply, conversation)`. The reply is
    what the customer reads; whatever typed result produced it is the
    implementation's own business."""

    reviewer: Callable[[str, str], Offstage] | None = None
    """`(decision, by) -> an approver`, where decision is grant · refuse · never.
    `None` says this implementation has no approval queue — which is a legitimate
    shape, and a scenario that needs one will fail loudly rather than silently
    passing against an agent that cannot approve anything."""

    colleague: Callable[[str, str], Offstage] | None = None
    """`(resolution, by) -> a desk`. `None` says no escalation desk."""


__all__ = ["Offstage", "Subject"]
