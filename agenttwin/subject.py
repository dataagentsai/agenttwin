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

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol


class Offstage(Protocol):
    """A human who acts between turns and never speaks to the agent.

    `await review(at=moment)` — look at the queue once, at scenario time
    `moment` (seconds), and act on whatever is due. `Approver` and `Desk` are
    the two AgentTwin ships.

    The runner also reads what it did: a colleague's `handled` and a
    reviewer's `reviewed`, each a sequence of per-pass records carrying
    `escalation_id` or `approval_id` and `outcome`. `handed_off` and `decided`
    are judged from them (SPEC, *What a scenario's checks read*)."""

    async def review(self, *, at: int | None = None) -> Any: ...


class Queued(Protocol):
    """One item in a queue an offstage human works — an approval or an
    escalation. Only these two attributes are read."""

    @property
    def id(self) -> str: ...

    @property
    def created_at(self) -> int:
        """Scenario time (seconds) the item was raised; a reviewer's or a desk's
        `delay_s` is counted from here."""
        ...


class Queue(Protocol):
    """The store an `Approver` or `Desk` is handed.

    `await store.pending()` returns what is waiting now. The store is passed
    back, untouched, as the first argument of `decide` / `close`, so it may
    carry whatever else those need."""

    async def pending(self) -> Iterable[Queued]: ...


class Say(Protocol):
    """`await say(text, customer_id, conversation) -> (reply, conversation)`.

    `conversation` is `None` on the first turn and thereafter whatever the
    previous call returned — opaque to the runner, so the implementation keeps
    its own state in it."""

    def __call__(
        self, text: str, customer_id: str, conversation: Any, /
    ) -> Awaitable[tuple[str, Any]]: ...


class OffstageFactory(Protocol):
    """`factory(decision, by, delay_s) -> Offstage`, called once per run.

    - `decision` — for a reviewer `grant` · `refuse` · `never` · `grant-twice`;
      for a colleague `handled` · `never` (the scenario's `approver.decides` /
      `desk.resolves`, passed through as written).
    - `by` — who acts, for the audit trail.
    - `delay_s` — seconds after an item's `created_at` before they act.

    Called synchronously; the returned object's `review` is awaited."""

    def __call__(self, decision: str, by: str, delay_s: int, /) -> Offstage: ...


@dataclass(frozen=True)
class Subject:
    """One implementation, mid-run, as a scenario sees it.

    Fields (each contract is a Protocol in this module):

    - `say: Say` — `async (text, customer_id, conversation) -> (reply,
      conversation)`. Required.
    - `reviewer: OffstageFactory | None` — `(decision, by, delay_s) -> Offstage`,
      usually an `Approver`. `None`: no approval queue; a scenario with an
      `approver` raises `Unrunnable`.
    - `colleague: OffstageFactory | None` — the same shape, usually a `Desk`.
      `None`: no escalation desk.
    - `opens: async (customer_id) -> str | None` — what the customer is shown
      on opening a conversation; `None` if nothing is.
    """

    say: Say
    """`(text, customer_id, conversation) -> (reply, conversation)`. The reply is
    what the customer reads; whatever typed result produced it is the
    implementation's own business."""

    reviewer: OffstageFactory | None = None
    """`(decision, by, delay_s) -> an approver`, where decision is grant · refuse
    · never and `delay_s` is how long this reviewer takes before deciding.
    `None` says this implementation has no approval queue — which is a legitimate
    shape, and a scenario that needs one will fail loudly rather than silently
    passing against an agent that cannot approve anything.

    **`delay_s` is the third argument because a reviewer who is instantaneous is
    not a reviewer.** It arrived late: `after_turns` sat in the scenario format,
    documented, for as long as the format existed, and no runner read it — so
    every scenario that said *the reviewer comes after two turns* got one who
    came immediately, and the approval window could never close on anybody
    (F-036)."""

    colleague: OffstageFactory | None = None
    """`(resolution, by, delay_s) -> a desk`. `None` says no escalation desk."""

    opens: Callable[[str], Awaitable[str]] | None = None
    """`(customer_id) -> what the customer is shown` when they open a conversation,
    before saying anything. `None` says this implementation shows nothing on
    opening, and a scenario whose actor `opens` fails loudly against it."""


__all__ = ["Offstage", "OffstageFactory", "Queue", "Queued", "Say", "Subject"]
