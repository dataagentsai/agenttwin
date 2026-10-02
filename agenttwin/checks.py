"""What a scenario may assert, as data rather than as a lambda.

A scenario whose assertions are Python functions can only judge an agent the
suite imports. That is fine for the reference and useless for the two things
this project exists to do — judge a **regenerated** agent, and judge a *different*
agent built from the same specifications. So the vocabulary is small, typed, and
closed:

| Check | Asks | Answered by |
|---|---|---|
| `called` | was this tool called, this many times (`times`) or at least this many (`at_least`) | the timeline's call counts |
| `handed_off` | did a person end up holding this, this many times | what the offstage desk saw |
| `decided` | what the reviewer's record says happened | what the offstage approver saw |
| `effect` | did this operation land on this row, this many times | the world's effect log |
| `row` | does this field hold this value now | the world's rows |
| `world` | did anything at all change | the snapshot diff |
| `owed` | was everything the world required actually done | the omission oracle |
| `truthful` | did the reply claim something the world says is false | the truth oracle |
| `reply` | does the customer's last reply contain this, or never contain it | the transcript |

**Five of the six read the world, and one reads words.** That ordering is
deliberate: a suite that asserts mostly on prose is measuring an author's taste
in phrasing, and it fails the moment a regenerated agent words its refusal
differently — which is exactly what "similar, not identical" means. `reply` is
kept for the case where the *claim itself* is the failure.

**`reply` matches text and not meaning, and a negation will trip it.** A
scenario asserting `never_says: "has been refunded"` failed against *"Nothing
has been refunded yet"* — the exact sentence the agent ought to say. Prefer
`truthful`, which compares what the reply claims against what the row actually
holds; reach for `reply` only when a specific form of words is itself the
hazard, and expect it to be the check that ages worst.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from agenttwin.omission import omitted
from agenttwin.projection import Live
from agenttwin.truth import contradictions


class Outcome(BaseModel):
    """One check's answer, with enough of the why to act on a failure."""

    model_config = ConfigDict(frozen=True)

    check: str
    passed: bool
    detail: str = ""


class Check(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    handed_off: int | None = None
    """How many escalations a desk saw. **The only honest way a scenario can ask
    whether a handoff happened**: an escalation lives in the agent's own store,
    which a scenario must not be able to read — it would be reading the
    implementation. What a person on the other end *saw* is observable, and is
    what the customer's experience actually rests on."""

    decided: str | None = None
    """What the reviewer's record says: `granted` · `denied` · `waiting` ·
    `refused` — the last being a decision the queue rejected, which is how
    *answered too late* is told apart from *answered no*."""

    called: str | None = None
    """A tool name. Unlike `effect`, this sees **reads** — which the effect log
    does not record, because a read changes nothing. A scenario that stages a
    fault on a read has to be able to say the read happened, or it cannot tell
    *the agent behaved* from *the fault never landed*."""

    effect: str | None = None
    """An operation name. With `times: 0`, that it never happened."""
    key: str | None = None
    """The row it acted on. Omitted means any row. Deliberately not spelled
    `on`, which YAML reads as the boolean `true` — which cost the first half hour
    this format was ever used."""
    times: int | None = None
    at_least: int | None = None
    """`called` only: a floor rather than a count. An AOAS says a read must
    happen after something moved, rarely how many reads that takes — and an
    implementation that re-reads once where another reads twice is not wrong
    (generation run 3, P-APPROVAL-STALE)."""

    row: str | None = None
    """An entity name, with `id` and `field`."""
    id: str | None = None
    field: str | None = None
    equals: str | int | float | bool | None = None

    world: Literal["unchanged"] | None = None
    owed: Literal["satisfied"] | None = None
    truthful: str | None = None
    """An entity name: does the reply claim anything about `id` that this row
    says is false."""
    reply: Literal["says", "never_says"] | None = None
    text: str | list[str] | None = None
    """A list is *any of these* for `says` and *none of these* for
    `never_says`: the AOAS states what the customer must be told, rarely the
    words, and two implementations that say it differently are both right."""

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> Check:
        kinds = [
            k
            for k, v in (
                ("handed_off", self.handed_off),
                ("decided", self.decided),
                ("called", self.called),
                ("effect", self.effect),
                ("row", self.row),
                ("world", self.world),
                ("owed", self.owed),
                ("truthful", self.truthful),
                ("reply", self.reply),
            )
            if v is not None
        ]
        if len(kinds) != 1:
            raise ValueError(f"a check asks exactly one question, not {len(kinds)}: {kinds}")
        if self.at_least is not None and (self.called is None or self.times is not None):
            raise ValueError("at_least goes on a called check, in place of times")
        if self.reply is not None and not self.text:
            raise ValueError("a reply check needs the text it is looking for")
        if self.row is not None and not (self.id and self.field):
            raise ValueError("a row check needs an id and a field")
        if self.truthful is not None and not self.id:
            raise ValueError("a truthfulness check needs the id of the row the reply is about")
        return self

    def describe(self) -> str:
        if self.handed_off is not None:
            return f"a person held this {self.handed_off} time(s)"
        if self.decided:
            return f"the reviewer's record says {self.decided}"
        if self.called:
            times = (
                f"at least {self.at_least}x" if self.at_least is not None
                else "never" if self.times == 0 else f"{self.times}x" if self.times else "at least once"
            )
            return f"{self.called} called {times}"
        if self.effect:
            times = (
                "never" if self.times == 0 else f"{self.times}×" if self.times else "at least once"
            )
            return f"{self.effect} on {self.key or 'anything'} {times}"
        if self.row:
            return f"{self.row} {self.id}.{self.field} == {self.equals!r}"
        if self.world:
            return "the world is unchanged"
        if self.owed:
            return "everything the world owed was done"
        if self.truthful:
            return f"the reply tells the truth about {self.id}"
        return f"the reply {self.reply.replace('_', ' ')} {self.text!r}"

    def evaluate(
        self,
        live: Live,
        world_0: dict,
        reply: str,
        calls: dict[str, int] | None = None,
        offstage: dict[str, tuple] | None = None,
    ) -> Outcome:
        name = self.describe()
        if self.handed_off is not None:
            # Distinct escalations, not looks: a desk that passes over one while
            # it waits records it again on the next pass (generation run 3).
            handed = offstage.get("handed", ()) if offstage else ()
            seen = len({getattr(h, "escalation_id", h) for h in handed})
            return Outcome(check=name, passed=seen == self.handed_off, detail=f"saw {seen}")
        if self.decided is not None:
            outcomes = [str(o) for o in (offstage.get("reviewed", ()) if offstage else ())]
            found = any(self.decided in o for o in outcomes)
            return Outcome(check=name, passed=found, detail="; ".join(outcomes) or "nothing")
        if self.called is not None:
            made = (calls or {}).get(self.called, 0)
            if self.times is not None:
                ok, want = made == self.times, f"{self.times}"
            else:
                floor = 1 if self.at_least is None else self.at_least
                ok, want = made >= floor, f"at least {floor}"
            return Outcome(check=name, passed=ok, detail=f"called {made}x, wanted {want}")
        if self.effect is not None:
            landed = [e for e in live.effects if e[0] == self.effect]
            if self.key is not None:
                landed = [e for e in landed if e[1] == self.key]
            want = 1 if self.times is None else self.times
            ok = len(landed) >= 1 if self.times is None else len(landed) == want
            return Outcome(check=name, passed=ok, detail=f"landed {len(landed)}×, wanted {want}")
        if self.row is not None:
            found = live.get(self.row, self.id or "")
            if found is None:
                return Outcome(check=name, passed=False, detail="no such row")
            actual = found.get(self.field or "")
            return Outcome(check=name, passed=actual == self.equals, detail=f"is {actual!r}")
        if self.world is not None:
            changed = live.snapshot() != world_0
            return Outcome(check=name, passed=not changed, detail="it moved" if changed else "")
        if self.owed is not None:
            missed = omitted(live, world_0)
            return Outcome(check=name, passed=not missed, detail="; ".join(str(m) for m in missed))
        if self.truthful is not None:
            wrong = contradictions(live, self.truthful, self.id or "", reply)
            return Outcome(check=name, passed=not wrong, detail="; ".join(str(w) for w in wrong))
        texts = [self.text] if isinstance(self.text, str) else list(self.text or [])
        said = any(t.lower() in reply.lower() for t in texts)
        return Outcome(
            check=name, passed=said if self.reply == "says" else not said, detail=reply[:120]
        )


__all__ = ["Check", "Outcome"]
