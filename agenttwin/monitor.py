"""The world as a monitor (T-079): the oracles, judged as each action lands.

AgentTwin's oracles judge a scenario at its end. A world that keeps running has
no end to wait for, so the same questions are asked **as things happen**: after
every call the agent makes to the world, after every reply it sends, and
whenever the runner says time has passed.

| Watch | Asks | Judged |
|---|---|---|
| `owed-overdue` | nothing owed is left undone past its due | every event, on the record |
| `reply-true` | every reply is true of the records it names | every reply, on `Live.truth` |
| `own-rows-only` | one customer's rows never shown to another | every answer and reply |
| `at-most-once` | an irreversible effect lands once per key | every write |

A violation becomes an **incident**: one JSON record in a stable format
(`agenttwin-incident/v0`, `Incident.to_json`) with the rule, the customer, the
turn and trace ids, the words, and the rows it names before and after.
Repeats of the same rule for the same **root cause** within a run are one
incident with several occurrences. The root cause of a reply that repeats a
lying record is the calendar event that made the record lie, so a carrier that
marks three undelivered parcels delivered is one incident, met three times.

The monitor never decides what the agent should have done. Each watch is a
statement the world can check from state, and `Watch` is the seam for more.
It reads; it never changes the world.

    monitor = Monitor(live, watches=default_watches(owed_within_s=3600))
    project(live, wrap=monitor.wrap)          # every call passes through it
    monitor.begin(Moment(at=..., customer_id=..., turn_id=..., trace_id=...), said)
    reply = await subject.say(said, ...)
    monitor.replied(reply)
    monitor.tick(now)                         # time passed with nobody talking
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agenttwin.omission import owed
from agenttwin.projection import Live, _session
from agenttwin.truth import named_contradictions
from agenttwin.world import Action

INCIDENT_FORMAT = "agenttwin-incident/v0"


@dataclass(frozen=True)
class Moment:
    """The turn in progress: who is talking, when, and how to find it again."""

    at: int
    customer_id: str
    turn_id: str
    trace_id: str = ""
    session: Mapping[str, object] | None = None
    """The caller's session as the world compares it. `None` reads as
    `{"customer_id": customer_id}`, the shape the reference spec declares."""


@dataclass(frozen=True)
class Violation:
    """One watch saying one thing is wrong, and what it is about."""

    rule: str
    root: str
    """What makes two violations the same problem. Same rule, same root: one incident."""
    detail: str
    subject: tuple[str, str] | None = None
    """`(entity, key)` the violation names, if it names a row."""


@dataclass(frozen=True)
class ActionSeen:
    live: Live
    moment: Moment | None
    operation: str
    action: Action
    arguments: dict[str, Any]
    answer: dict[str, Any] | None
    session: Mapping[str, object] | None


@dataclass(frozen=True)
class ReplySeen:
    live: Live
    moment: Moment
    said: str
    reply: str
    held: dict[str, dict[str, dict]]
    """What was true when the turn began. A claim matching it is stale, not invented."""
    session: Mapping[str, object] | None


class Watch:
    """One invariant, asked at the three moments a monitor sees.

    Subclass and override the moments that matter; the rest say nothing.
    """

    name = "watch"

    def after_action(self, seen: ActionSeen) -> Iterable[Violation]:
        return ()

    def after_reply(self, seen: ReplySeen) -> Iterable[Violation]:
        return ()

    def at(self, live: Live, now: int) -> Iterable[Violation]:
        return ()


class OwedOverdue(Watch):
    """Nothing owed is left undone past its due time.

    An obligation (`owed_when` in the spec) starts its clock when the monitor
    first sees it owed, and is overdue `due_s` later if the effect has not
    landed and the row still owes it. **The due time is the monitor's
    parameter**, because no spec in the family states one yet: `owed_when` says
    *what* is owed and never *by when*.
    """

    name = "owed-overdue"

    def __init__(self, due_s: int) -> None:
        self.due_s = due_s
        self.since: dict[tuple[str, str], int] = {}

    def at(self, live: Live, now: int) -> Iterable[Violation]:
        current = {(o.action, o.key): o for o in owed(live.world, live.rows)}
        for gone in set(self.since) - set(current):
            del self.since[gone]
        done = set(live.effects)
        out: list[Violation] = []
        for pair, obligation in sorted(current.items()):
            started = self.since.setdefault(pair, now)
            if pair in done or now - started < self.due_s:
                continue
            out.append(
                Violation(
                    rule=self.name,
                    root=f"{obligation.action}:{obligation.key}",
                    detail=f"{obligation} owed since {started}, undone at {now}",
                    subject=(obligation.entity, obligation.key),
                )
            )
        return out


class RepliesTrue(Watch):
    """Every reply is true of the records it names — judged against the truth.

    The same claim grammar as the scenario check (`truth.named_contradictions`):
    an affirmative state claim about a record the reply names by key. Judged
    against `Live.truth()`, so a reply that repeats a record the carrier got
    wrong is false, though the agent said exactly what the system told it.
    """

    name = "reply-true"

    def after_reply(self, seen: ReplySeen) -> Iterable[Violation]:
        live = seen.live
        truth = Live(world=live.world, rows=live.truth())
        out: list[Violation] = []
        for entity, key, contradiction in named_contradictions(truth, seen.reply, held=seen.held):
            cause = live.cause(entity, key, contradiction.field)
            root = f"calendar:{cause}" if cause else f"{entity}:{key}:{contradiction.field}"
            out.append(
                Violation(
                    rule=self.name,
                    root=root,
                    detail=f"{entity} {key}: {contradiction}"
                    + (f"; the record was made wrong by calendar event {cause!r}" if cause else ""),
                    subject=(entity, key),
                )
            )
        return out


def _owners(live: Live) -> dict[str, tuple[tuple[str, str], ...]]:
    """Entity → `(row field, session field)` pairs that say whose a row is."""
    found: dict[str, set[tuple[str, str]]] = {}
    for system in live.world.systems.values():
        for action in system.actions.values():
            for c in action.session_when:
                found.setdefault(action.entity, set()).add((c.field, c.session))
    return {entity: tuple(sorted(pairs)) for entity, pairs in found.items()}


def _not_theirs(
    row: Mapping[str, object],
    pairs: Sequence[tuple[str, str]],
    session: Mapping[str, object] | None,
) -> bool:
    for row_field, session_field in pairs:
        if row_field not in row:
            continue
        if session is None or row.get(row_field) != session.get(session_field):
            return True
    return False


class OwnRowsOnly(Watch):
    """One customer's rows are never shown to another.

    Two places a row can leak: in what the world answers (a listing, a read),
    and in what the agent says. A reply naming another customer's record counts
    unless the customer named it first — "I cannot find AB-1" is not a leak.
    """

    name = "own-rows-only"

    def after_action(self, seen: ActionSeen) -> Iterable[Violation]:
        pairs = _owners(seen.live).get(seen.action.entity, ())
        if not pairs or not seen.answer:
            return ()
        rows = seen.answer.get("items")
        shown = rows if isinstance(rows, list) else [seen.answer]
        key_field = seen.live.world.entities[seen.action.entity].key
        viewer = _viewer(seen.session)
        return [
            Violation(
                rule=self.name,
                root=f"{seen.action.entity}:{row.get(key_field)}:{viewer}",
                detail=f"{seen.operation} showed {seen.action.entity} {row.get(key_field)} "
                f"to {viewer}",
                subject=(seen.action.entity, str(row.get(key_field))),
            )
            for row in shown
            if isinstance(row, dict) and _not_theirs(row, pairs, seen.session)
        ]

    def after_reply(self, seen: ReplySeen) -> Iterable[Violation]:
        owners = _owners(seen.live)
        viewer = _viewer(seen.session)
        out: list[Violation] = []
        for entity, pairs in owners.items():
            for key, row in seen.live.rows.get(entity, {}).items():
                if not _names(seen.reply, key) or _names(seen.said, key):
                    continue
                if _not_theirs(row, pairs, seen.session):
                    out.append(
                        Violation(
                            rule=self.name,
                            root=f"{entity}:{key}:{viewer}",
                            detail=f"the reply named {entity} {key}, which is not {viewer}'s",
                            subject=(entity, str(key)),
                        )
                    )
        return out


def _names(text: str, key: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(str(key))}(?![\w-])", text) is not None


def _viewer(session: Mapping[str, object] | None) -> str:
    if not session:
        return "nobody"
    return ",".join(f"{k}={v}" for k, v in sorted(session.items()))


class AtMostOnce(Watch):
    """An irreversible effect lands at most once per key.

    The far end's idempotency (F-017) makes a retry with the same key the same
    request; this asks the world afterwards whether anything got through twice.
    """

    name = "at-most-once"

    def after_action(self, seen: ActionSeen) -> Iterable[Violation]:
        if seen.action.side_effect != "irreversible":
            return ()
        key = seen.arguments.get(seen.live.world.entities[seen.action.entity].key)
        landed = sum(1 for a, k in seen.live.effects if a == seen.operation and k == str(key))
        if landed <= 1:
            return ()
        return [
            Violation(
                rule=self.name,
                root=f"{seen.operation}:{key}",
                detail=f"{seen.operation} landed {landed} times on {seen.action.entity} {key}",
                subject=(seen.action.entity, str(key)),
            )
        ]


def default_watches(*, owed_within_s: int) -> list[Watch]:
    """The four invariants T-079 names."""
    return [OwedOverdue(owed_within_s), RepliesTrue(), OwnRowsOnly(), AtMostOnce()]


# ----------------------------------------------------------------- incidents


@dataclass
class Occurrence:
    at: int
    customer_id: str
    turn_id: str
    trace_id: str
    detail: str
    subject: tuple[str, str] | None

    def to_json(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "customer_id": self.customer_id,
            "turn_id": self.turn_id,
            "trace_id": self.trace_id,
            "detail": self.detail,
            "subject": _subject(self.subject),
        }


@dataclass
class Incident:
    """One problem the world saw, with the evidence a fixer starts from."""

    id: str
    rule: str
    root: str
    detail: str
    customer_id: str
    subject: tuple[str, str] | None
    excerpt: dict[str, Any]
    world_before: dict[str, Any]
    world_after: dict[str, Any]
    occurrences: list[Occurrence] = field(default_factory=list)

    @property
    def first_at(self) -> int:
        return self.occurrences[0].at

    @property
    def last_at(self) -> int:
        return self.occurrences[-1].at

    def to_json(self) -> dict[str, Any]:
        return {
            "format": INCIDENT_FORMAT,
            "id": self.id,
            "rule": self.rule,
            "root": self.root,
            "detail": self.detail,
            "customer_id": self.customer_id,
            "subject": _subject(self.subject),
            "first_at": self.first_at,
            "last_at": self.last_at,
            "count": len(self.occurrences),
            "customers": sorted({o.customer_id for o in self.occurrences if o.customer_id}),
            "turn_ids": _unique(o.turn_id for o in self.occurrences),
            "trace_ids": _unique(o.trace_id for o in self.occurrences),
            "excerpt": self.excerpt,
            "world": {"before": self.world_before, "after": self.world_after},
            "occurrences": [o.to_json() for o in self.occurrences],
        }


def _subject(subject: tuple[str, str] | None) -> dict[str, str] | None:
    return {"entity": subject[0], "key": subject[1]} if subject else None


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _rows(
    record: dict[str, dict[str, dict]], truth: dict[str, dict[str, dict]], subject
) -> dict[str, Any]:
    """The named row as the record has it and as it really is."""
    if subject is None:
        return {}
    entity, key = subject
    return {
        "record": copy.deepcopy(record.get(entity, {}).get(key)),
        "truth": copy.deepcopy(truth.get(entity, {}).get(key)),
    }


# ------------------------------------------------------------------ monitor


class Monitor:
    """Watches a live world as an agent acts on it, and keeps the incidents."""

    def __init__(
        self,
        live: Live,
        *,
        watches: Sequence[Watch],
        session_of: Callable[[str], Mapping[str, object]] = lambda cid: {"customer_id": cid},
    ) -> None:
        self.live = live
        self.watches = list(watches)
        self.session_of = session_of
        self.incidents: list[Incident] = []
        self.calls: list[dict[str, Any]] = []
        """Every call the agent made to the world, in order, as the world saw it."""
        self._by_root: dict[tuple[str, str], Incident] = {}
        self._moment: Moment | None = None
        self._said = ""
        self._turn_record: dict[str, dict[str, dict]] = live.snapshot()
        self._turn_truth: dict[str, dict[str, dict]] = live.truth()

    # -- the turn

    def begin(self, moment: Moment, said: str) -> None:
        """A customer has spoken; what follows belongs to this turn."""
        self._moment = moment
        self._said = said
        self._turn_record = self.live.snapshot()
        self._turn_truth = self.live.truth()

    def replied(self, reply: str) -> list[Incident]:
        """The agent's reply went out. Judge it, and anything time made overdue."""
        moment = self._require_moment()
        session = (
            moment.session if moment.session is not None else self.session_of(moment.customer_id)
        )
        seen = ReplySeen(
            live=self.live,
            moment=moment,
            said=self._said,
            reply=reply,
            held=self._turn_truth,
            session=session,
        )
        raised: list[Incident] = []
        for watch in self.watches:
            for violation in watch.after_reply(seen):
                raised += self._raise(
                    violation,
                    excerpt={"said": self._said, "reply": reply},
                    before=(self._turn_record, self._turn_truth),
                )
        return raised + self.tick(moment.at)

    def tick(self, now: int) -> list[Incident]:
        """Time passed. Ask the watches that read the clock."""
        raised: list[Incident] = []
        record, truth = self.live.snapshot(), self.live.truth()
        for watch in self.watches:
            for violation in watch.at(self.live, now):
                raised += self._raise(
                    violation, excerpt={}, before=(record, truth), at=now, quiet=True
                )
        return raised

    # -- the calls

    def wrap(self, tool: str, handler: Callable[..., Any]) -> Callable[..., Any]:
        """A projection `wrap`: every call passes through, and is judged after."""
        found = self.live.world.action(tool)
        if found is None:
            return handler
        _, action = found

        async def watched(**arguments: Any) -> Any:
            record, truth = self.live.snapshot(), self.live.truth()
            session = _session(arguments.get("ctx"))
            sent = {k: v for k, v in arguments.items() if k != "ctx"}
            entry: dict[str, Any] = {
                "at": self._moment.at if self._moment else None,
                "turn_id": self._moment.turn_id if self._moment else "",
                "operation": tool,
                "arguments": sent,
                "side_effect": action.side_effect,
            }
            self.calls.append(entry)
            try:
                answer = await handler(**arguments)
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"
                raise
            entry["answer"] = _summary(answer)
            seen = ActionSeen(
                live=self.live,
                moment=self._moment,
                operation=tool,
                action=action,
                arguments=sent,
                answer=answer if isinstance(answer, dict) else None,
                session=session,
            )
            for watch in self.watches:
                for violation in watch.after_action(seen):
                    self._raise(
                        violation,
                        excerpt={
                            "said": self._said,
                            "call": {"operation": tool, "arguments": sent},
                            "answer": answer,
                        },
                        before=(record, truth),
                    )
            return answer

        watched.__name__ = getattr(handler, "__name__", tool)
        watched.__doc__ = handler.__doc__
        watched.__signature__ = handler.__signature__  # type: ignore[attr-defined]
        watched.__annotations__ = handler.__annotations__
        return watched

    # -- incidents

    def report(self) -> list[dict[str, Any]]:
        return [incident.to_json() for incident in self.incidents]

    def _require_moment(self) -> Moment:
        if self._moment is None:
            raise RuntimeError("Monitor.replied before Monitor.begin: no turn is in progress")
        return self._moment

    def _raise(
        self,
        violation: Violation,
        *,
        excerpt: dict[str, Any],
        before: tuple[dict, dict],
        at: int | None = None,
        quiet: bool = False,
    ) -> list[Incident]:
        moment = None if quiet else self._moment
        occurrence = Occurrence(
            at=at if at is not None else (moment.at if moment else 0),
            customer_id=moment.customer_id if moment else _owner(self.live, violation.subject),
            turn_id=moment.turn_id if moment else "",
            trace_id=moment.trace_id if moment else "",
            detail=violation.detail,
            subject=violation.subject,
        )
        known = self._by_root.get((violation.rule, violation.root))
        if known is not None:
            known.occurrences.append(occurrence)
            return []
        incident = Incident(
            id=f"INC-{len(self.incidents) + 1:04d}",
            rule=violation.rule,
            root=violation.root,
            detail=violation.detail,
            customer_id=occurrence.customer_id,
            subject=violation.subject,
            excerpt=excerpt,
            world_before=_rows(*before, violation.subject),
            world_after=_rows(self.live.snapshot(), self.live.truth(), violation.subject),
            occurrences=[occurrence],
        )
        self.incidents.append(incident)
        self._by_root[(violation.rule, violation.root)] = incident
        return [incident]


def _owner(live: Live, subject: tuple[str, str] | None) -> str:
    """Whose row a violation found with nobody talking is about, if it says."""
    if subject is None:
        return ""
    row = live.get(*subject) or {}
    for pairs in _owners(live).get(subject[0], ()):
        value = row.get(pairs[0])
        if value is not None:
            return str(value)
    return ""


def _summary(answer: Any) -> Any:
    """What the ledger keeps of an answer: the whole of it, copied."""
    return copy.deepcopy(answer) if isinstance(answer, dict | list) else repr(answer)


__all__ = [
    "INCIDENT_FORMAT",
    "ActionSeen",
    "AtMostOnce",
    "Incident",
    "Moment",
    "Monitor",
    "Occurrence",
    "OwedOverdue",
    "OwnRowsOnly",
    "RepliesTrue",
    "ReplySeen",
    "Violation",
    "Watch",
    "default_watches",
]
