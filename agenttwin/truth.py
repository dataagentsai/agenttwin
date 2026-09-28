"""Was the answer true?

The missing half of stage 6. The world diff answers *"did the agent change
something it should not have"*, and for a question — *"where is my order?"* —
the answer is trivially no, because a question changes nothing. Every predicate
written against the diff then passes without checking anything.

That is not a gap in coverage, it is a **false pass**: the suite reports success
by being unable to see. Five of the support agent's ten scenarios are questions,
and reads are the majority of real support traffic, so this is the common case
rather than an edge one.

## Why a direct check rather than a judge or a metamorphic relation

Metamorphic testing is the established answer when correct output is *unknowable*
— you cannot say what the right answer is, so you assert a relation between two
answers instead. That is the right tool for a research agent over a corpus, and
doc 29 keeps it for that.

It is the wrong tool here, because **we are not in that situation**. AgentTwin
owns the world. It knows that AB-10001 is `shipped`. When the agent says
"delivered", we do not need a relation or a judge to know it is wrong — we can
look. An oracle that has ground truth should use it; reaching for a weaker
instrument when a stronger one is available is how a test suite ends up
measuring its own cleverness.

## What counts as a claim

Only **affirmative assertions about this order's declared state**. That
restriction is not caution, it is F-004 repeating: the first version of the
policy grounding matched the bare status word, so *"that order has shipped, so it
can no longer be cancelled"* was flagged — the word "cancelled" appears, and the
sentence is a correct refusal. A check that fires on correct behaviour gets
switched off, which leaves nothing.

So a claim needs a subject and a copula — *"your order is delivered"*, *"it has
shipped"* — and is discarded when it sits under a modal, a negation or a
condition, where the sentence is explaining a rule rather than reporting a fact.

## The limit, stated

Declared **enum** fields only, on one named entity. A reply that miscounts, that
invents a fact with no state word in it, or that is wrong about an integer field
still passes here. Those need either a judge or the metamorphic route, and both
are worth doing after this, not instead of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agenttwin.projection import Live
from agenttwin.world import Entity

SUBJECT = r"(?:your|the|this|that)?\s*(?:order|item|package|parcel|it)(?:\s+[A-Z]{1,3}-\d{3,8})?"
"""The optional identifier matters: the deterministic route answers *"Order
AB-10001 is currently shipped"*, and a subject pattern that stopped at the noun
would miss every reply the agent produces without calling the model at all."""

COPULA = r"(?:is|was|has|have|had)?\s*(?:been|now|still|currently|already)?\s*"

GUARDS = re.compile(
    r"\b(?:once|if|when|unless|whether|before|after|until|cannot|can't|could|"
    r"would|should|might|may|no longer|not|never|isn't|wasn't)\b",
    re.I,
)
"""Words that turn a report into an explanation.

Checked in the run-up to a match rather than baked into one pattern, because the
list grows: every one of these was found by a sentence a correct agent produced.
"""

LOOKBACK = 45
"""How far back to look for a guard. Long enough for *"an order that is picked
can no longer be cancelled"*, short enough not to swallow a previous sentence."""


@dataclass(frozen=True)
class Claim:
    """An assertion the reply makes about a field's value."""

    field: str
    value: str
    phrase: str


@dataclass(frozen=True)
class Contradiction:
    """A claim the world disagrees with."""

    field: str
    claimed: str
    actual: str
    phrase: str

    def __str__(self) -> str:
        return f"said {self.field} is {self.claimed!r} when it is {self.actual!r} ({self.phrase!r})"


def _pattern(value: str) -> re.Pattern[str]:
    """`out_for_delivery` is written *out for delivery* by anything human."""
    spoken = re.escape(value.replace("_", " ")).replace(r"\ ", r"\s+")
    return re.compile(rf"{SUBJECT}\s+{COPULA}{spoken}\b", re.I)


def claims(reply: str, entity: Entity) -> tuple[Claim, ...]:
    """Every affirmative state assertion in the reply, per declared enum field."""
    found: list[Claim] = []
    for name, spec in entity.fields.items():
        if spec.type != "enum":
            continue
        for value in spec.values:
            for match in _pattern(value).finditer(reply):
                run_up = reply[max(0, match.start() - LOOKBACK) : match.start()]
                if GUARDS.search(run_up) or GUARDS.search(match.group(0)):
                    continue  # explaining a rule, not reporting a fact
                found.append(Claim(field=name, value=value, phrase=match.group(0).strip()))
    return tuple(found)


def contradictions(live: Live, entity: str, key: str, reply: str) -> tuple[Contradiction, ...]:
    """Claims this reply makes that the world says are false."""
    row = live.get(entity, key)
    if row is None:
        return ()
    spec = live.world.entities[entity]

    return tuple(
        Contradiction(
            field=claim.field,
            claimed=claim.value,
            actual=str(row[claim.field]),
            phrase=claim.phrase,
        )
        for claim in claims(reply, spec)
        if claim.field in row and str(row[claim.field]) != claim.value
    )


def answer_is_true(entity: str, key: str):
    """A ready-made scenario predicate: nothing the agent said contradicts the world.

    Written as a factory so a scenario reads as prose —

        predicates={"the answer was true": answer_is_true("order", "AB-10001")}

    — and so the check is *declared* alongside the world-diff predicates rather
    than being something each test remembers to do.
    """

    def predicate(live: Live, transcript) -> bool:
        return not any(contradictions(live, entity, key, turn.heard) for turn in transcript.turns)

    return predicate


def named_contradictions(
    live: Live, reply: str, *, held: dict | None = None
) -> tuple[tuple[str, str, Contradiction], ...]:
    """`(entity, key, contradiction)` for every record the reply **names**.

    The per-scenario `truthful` check asks about one row the author thought of.
    A reply that names a different one is unchecked, and an agent with no output
    guard of its own passed two thirds of the reference's suite while telling
    the customer *"Order AB-10002 has been cancelled"* on every turn (runner
    negative control, 2026-09-28). So every scenario asks this as well, of every
    reply, against the world as it was when the reply was given.

    Each named key is judged on its own stretch of the reply — from the start
    of its sentence (never before the previous key named) up to the next key —
    so *"AB-10001 has shipped and AB-10002 is pending"* is two claims about two
    rows, not four about each. A claim naming no record (*"that order"*) is out of reach here:
    it cannot be attributed without guessing, and a guess that fired on a
    correct agent would get the check switched off (F-004).

    `held` is the world's rows when the turn began (`Live.snapshot()`). A claim
    matching what the row held then is **stale, not invented**, and is not
    reported: the reference's `stale-read-then-refused` reads an order as
    pending, the world ships it before the reply is read, and the agent said
    exactly what it was told. Staleness is a real hazard with its own checks
    (freshness, AHC-0107); this one is for claims the world never supported.
    """
    mentions: list[tuple[int, int, str, str]] = []
    for entity, spec in live.world.entities.items():
        if not any(f.type == "enum" for f in spec.fields.values()):
            continue
        for key in live.rows.get(entity, {}):
            for match in re.finditer(rf"(?<![\w-]){re.escape(str(key))}(?![\w-])", reply):
                mentions.append((match.start(), match.end(), entity, str(key)))
    mentions.sort()
    found: list[tuple[str, str, Contradiction]] = []
    for i, (start, _end, entity, key) in enumerate(mentions):
        # Back to the start of its own sentence, but never past the previous
        # key: "Order AB-1 …" keeps its noun, and a claim about the record named
        # before it stays that record's.
        sentence = max(reply.rfind(mark, 0, start) for mark in ".!?\n") + 1
        begin = max(mentions[i - 1][1] if i else 0, sentence)
        stop = mentions[i + 1][0] if i + 1 < len(mentions) else len(reply)
        stretch = reply[begin:stop]
        before = ((held or {}).get(entity) or {}).get(key) or {}
        found += [
            (entity, key, c)
            for c in contradictions(live, entity, key, stretch)
            if str(before.get(c.field)) != c.claimed
        ]
    return tuple(found)


__all__ = [
    "Claim",
    "Contradiction",
    "answer_is_true",
    "claims",
    "contradictions",
    "named_contradictions",
]
