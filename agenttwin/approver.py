"""The second human.

A human approver is **an actor and a queue**. An agent builds the queue —
expiry, self-approval refusal, terminal decisions, an idempotency key — and the
actor is easy never to build.

Without one, the approval path is only ever exercised against a reviewer who
is instantaneous, always available, and always says yes. That reviewer does not
exist. And the path guarded by them is the one that moves real money, which
makes the least-modelled participant the guard on the highest-value action.

## What an actor adds that a function call does not

Every existing test decides by calling `decide(...)` inline, at the moment of its
choosing, with `granted=True`. That tests the *queue*. It cannot express the four
things a real reviewer does:

- **takes time** — and the customer is still in the conversation while they do;
- **says no** — and the customer must be told something true about that;
- **walks away** — a pending approval nobody ever answers is the common case in
  any real operations queue, not an edge one;
- **answers too late** — after the grant window has closed, which is a *third*
  outcome distinct from yes and no, and the one most likely to be mishandled.

## Why the decision function is injected

`agenttwin` must not import the agent under test. An import contract enforces
one direction — the agent cannot see its simulator — and this is the same principle
pointing the other way: a simulator that imported this agent's approval module
would be a simulator for that agent only.

So the actor owns **when** and **what** it decides; the agent's own module owns
**whether that is allowed**. Which is also the honest division of labour: a human
reviewer does not implement the expiry rule, they run into it.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from agenttwin.actor import Determinism
from agenttwin.subject import Queue, Queued


class Decide(Protocol):
    """`await decide(store, approval_id, *, granted, by, now)` — the agent's own
    rule for recording a decision, injected.

    - `store` — the `Queue` the `Approver` was built with, passed back as is.
    - `approval_id` — a `Queued.id` from `store.pending()`.
    - `granted` — `True` to grant, `False` to deny.
    - `by` — the reviewer's name (`Approver.name`).
    - `now` — scenario time in seconds, the moment the decision is made.

    Return value ignored. **Raise to reject the decision** (expired, already
    decided, self-approval): the reviewer records it as `refused` with the
    exception's text, which is how *answered too late* is observed."""

    def __call__(
        self, store: Any, approval_id: str, /, *, granted: bool, by: str, now: int
    ) -> Awaitable[object]: ...


class Decision(StrEnum):
    GRANT = "grant"
    DENY = "deny"
    SILENCE = "silence"
    """Never answers. Not a failure to configure — a reviewer who went home."""
    GRANT_TWICE = "grant-twice"
    """Grants, and then comes back and grants the same thing again.

    Two people in a queue both picking up the same item, or one person clicking
    approve twice on a page that did not visibly change. Neither is exotic; both
    are what an approval queue looks like on a busy afternoon.

    The reason it earns a vocabulary entry of its own is what it costs when it
    works: a grant that can be re-granted is a grant that can be executed twice,
    and the effect behind this particular gate is money leaving the business."""


@dataclass(frozen=True)
class Review:
    """What became of one approval when the reviewer looked at it."""

    approval_id: str
    outcome: str
    """`granted` · `denied` · `waiting` · `refused` — the last when the queue
    rejected the decision, which is how *answered too late* surfaces."""
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.approval_id} {self.outcome}" + (f" ({self.detail})" if self.detail else "")


@dataclass
class Approver:
    """A person who reviews pending approvals — or does not.

    Deliberately not a subclass of the conversational actor. It does not speak to
    the agent and the agent never hears from it; it acts on the queue out of
    band, which is exactly how the real thing works.

    `Approver(store, decide, decision=..., delay_s=..., name=...)`:

    - `store` — a `Queue`: `await store.pending()` returns items with `.id` and
      `.created_at` (scenario seconds).
    - `decide` — a `Decide`: `await decide(store, id, granted=, by=, now=)`;
      raise to reject.
    - `await review(at=moment)` looks at the queue once. An item is decided once
      `moment >= created_at + delay_s`; `SILENCE` never decides. Returns, and
      appends to `reviewed`, one `Review` per item looked at.

    The classmethods `grants`, `denies`, `silent`, `grants_twice` set `decision`.
    """

    store: Queue
    decide: Decide
    decision: Decision = Decision.GRANT
    delay_s: int = 0
    """How long this reviewer takes. Set beyond the approval's TTL to model the
    reviewer who answers after the window closed."""

    name: str = "ops-7"
    """Who decided, recorded in the audit trail. Set to the customer's own id to
    exercise the confused-deputy refusal."""

    determinism: Determinism = Determinism.SCRIPTED
    reviewed: list[Review] = field(default_factory=list)
    open: dict[str, Queued] = field(default_factory=dict)
    """What this reviewer has seen in the queue and not yet decided.

    A queue may drop an approval that expired, and a reviewer who opened it
    before then still has it in front of them. Without this, a reviewer who
    answers too late never answers at all, and the refusal that is the whole
    point of the window can never be observed (T-028: a Temporal queue that
    drops expired approvals is what showed it)."""

    @classmethod
    def grants(cls, store, decide, **kw) -> Approver:
        return cls(store=store, decide=decide, decision=Decision.GRANT, **kw)

    @classmethod
    def denies(cls, store, decide, **kw) -> Approver:
        return cls(store=store, decide=decide, decision=Decision.DENY, **kw)

    @classmethod
    def silent(cls, store, decide, **kw) -> Approver:
        return cls(store=store, decide=decide, decision=Decision.SILENCE, **kw)

    @classmethod
    def grants_twice(cls, store, decide, **kw) -> Approver:
        return cls(store=store, decide=decide, decision=Decision.GRANT_TWICE, **kw)

    async def review(self, *, at: int | None = None) -> tuple[Review, ...]:
        """Look at the queue once, and decide whatever is due.

        Called between conversational turns, because that is when a real
        reviewer acts: while the customer is still there, and without either of
        them knowing what the other is doing.
        """
        moment = at if at is not None else int(time.time())
        for approval in await self.store.pending():
            self.open.setdefault(approval.id, approval)

        outcomes: list[Review] = []
        for approval in list(self.open.values()):
            review = await self._look_at(approval, moment)
            if review.outcome != "waiting":
                del self.open[approval.id]
            outcomes.append(review)
        # The second decision, on something that has already left the queue.
        # Deliberately after the queue pass and against an id this reviewer
        # granted itself, because that is the honest shape of it: nobody
        # re-decides an approval they have never seen.
        if self.decision is Decision.GRANT_TWICE:
            again = [r.approval_id for r in self.reviewed if r.outcome == "granted"]
            if again:
                outcomes.append(await self._decide(again[0], granted=True, moment=moment))
        self.reviewed.extend(outcomes)
        return tuple(outcomes)

    async def _look_at(self, approval, moment: int) -> Review:
        if self.decision is Decision.SILENCE:
            return Review(approval.id, "waiting", "nobody picked it up")
        if moment < approval.created_at + self.delay_s:
            return Review(approval.id, "waiting", f"reviewer takes {self.delay_s}s")

        granted = self.decision in (Decision.GRANT, Decision.GRANT_TWICE)
        return await self._decide(approval.id, granted=granted, moment=moment)

    async def _decide(self, approval_id: str, *, granted: bool, moment: int) -> Review:
        try:
            await self.decide(self.store, approval_id, granted=granted, by=self.name, now=moment)
        except Exception as refused:  # noqa: BLE001
            # The queue's rules belong to the agent, not to its simulator, so the
            # exception type is deliberately not imported. A reviewer who is told
            # "too late", or "that is already decided", experiences a refusal and
            # not a type.
            return Review(approval_id, "refused", str(refused))

        return Review(approval_id, "granted" if granted else "denied")


__all__ = ["Approver", "Decision", "Review"]
