"""What a scenario may assert, as data rather than as a lambda.

A scenario whose assertions are Python functions can only judge an agent the
suite imports. That is fine for the reference and useless for the two things
this project exists to do — judge a **regenerated** agent, and judge a *different*
agent built from the same specifications. So the vocabulary is small, typed, and
closed:

| Check | Asks | Answered by |
|---|---|---|
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
kept for the case where the *claim itself* is the failure, and `never_says` is
the safer half of it.
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

    effect: str | None = None
    """An operation name. With `times: 0`, that it never happened."""
    key: str | None = None
    """The row it acted on. Omitted means any row. Deliberately not spelled
    `on`, which YAML reads as the boolean `true` — which cost the first half hour
    this format was ever used."""
    times: int | None = None

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
    text: str | None = None

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> Check:
        kinds = [
            k
            for k, v in (
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
        if self.reply is not None and not self.text:
            raise ValueError("a reply check needs the text it is looking for")
        if self.row is not None and not (self.id and self.field):
            raise ValueError("a row check needs an id and a field")
        if self.truthful is not None and not self.id:
            raise ValueError("a truthfulness check needs the id of the row the reply is about")
        return self

    def describe(self) -> str:
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

    def evaluate(self, live: Live, world_0: dict, reply: str) -> Outcome:
        name = self.describe()
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
        said = (self.text or "").lower() in reply.lower()
        return Outcome(
            check=name, passed=said if self.reply == "says" else not said, detail=reply[:120]
        )


__all__ = ["Check", "Outcome"]
