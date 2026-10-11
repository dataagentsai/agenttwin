"""Parties that act on their own.

Tier 2. Everything so far drove the agent with a fixed question. An actor decides
what to say *next* based on what the agent just said, which is how a scenario
reaches states nobody thought to write down.

## Determinism is declared, never inferred

DD3, in code. Three classes, and the trade is explicit:

`SCRIPTED` — a fixed list of turns. Free, exactly reproducible, and only ever
walks the path someone already imagined.

`STATE_MACHINE` — reacts to the agent's reply through declared rules. Still free,
still exactly reproducible, and **can reach states no script contains**, because
the branch it takes depends on what the agent actually did. This is the useful
middle and where most value lives.

`MODEL_DRIVEN` — a model plays the customer. Finds what neither of the above
would, costs a call per turn, and destroys reproducibility.

**A run's determinism class is the weakest of its actors**, and the run record
states it. A reader must never have to guess whether a result can be reproduced.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum


class Determinism(StrEnum):
    SCRIPTED = "scripted"
    STATE_MACHINE = "state_machine"
    MODEL_DRIVEN = "model_driven"


ORDER = [Determinism.SCRIPTED, Determinism.STATE_MACHINE, Determinism.MODEL_DRIVEN]


def weakest(*classes: Determinism) -> Determinism:
    """The weakest link. A world with one model-driven actor is model-driven."""
    return max(classes, key=ORDER.index) if classes else Determinism.SCRIPTED


@dataclass
class Turn:
    said: str
    heard: str = ""


class ScriptedActor:
    """Says the same things in the same order, whatever it hears.

    Honest about its limitation: an agent that answers perfectly and an agent
    that ignores the question entirely produce the same transcript from a script.
    """

    determinism = Determinism.SCRIPTED

    def __init__(self, turns: Sequence[str], *, name: str = "customer") -> None:
        self.name = name
        self._turns = list(turns)
        self._position = 0
        self.heard: list[str] = []

    def next(self, reply: str) -> str | None:
        if reply:
            self.heard.append(reply)
        if self._position >= len(self._turns):
            return None
        turn = self._turns[self._position]
        self._position += 1
        return turn


@dataclass
class Rule:
    """If the agent's reply matches, say this next."""

    when: re.Pattern[str]
    say: str
    label: str = ""


class StateMachineActor:
    """Reacts to what the agent actually said.

    Deterministic and free, and still able to reach somewhere a script cannot —
    because the branch depends on the agent's behaviour rather than on a plan
    made before the run.

    It knows only what a customer knows: its own goal and the replies it has
    received. Giving an actor visibility of the world would let a scenario pass
    because the actor steered around a defect.
    """

    determinism = Determinism.STATE_MACHINE

    def __init__(
        self,
        opening: str,
        rules: Sequence[Rule],
        *,
        name: str = "customer",
        max_turns: int = 6,
        persistence: str | None = None,
    ) -> None:
        self.name = name
        self.opening = opening
        self.rules = list(rules)
        self.max_turns = max_turns
        self.persistence = persistence
        """What to say when nothing matched. `None` ends the conversation.

        A persistent customer is the interesting one: real people do not accept
        the first refusal, and an agent that holds a policy for one turn and
        concedes on the third has failed in a way no single-turn test sees.
        """
        self.turns = 0
        self.path: list[str] = []
        self.heard: list[str] = []

    def next(self, reply: str) -> str | None:
        if reply:
            self.heard.append(reply)
        if self.turns >= self.max_turns:
            return None
        self.turns += 1

        if self.turns == 1:
            self.path.append("opening")
            return self.opening

        for rule in self.rules:
            if rule.when.search(reply):
                self.path.append(rule.label or rule.say[:24])
                return rule.say

        if self.persistence is None:
            self.path.append("done")
            return None
        self.path.append("persist")
        return self.persistence


class ModelActor:
    """A model plays the customer, under a persona.

    Trades replay for realism, and the run record says so — a reader must never
    have to guess whether a result can be reproduced. Worth having when the
    question is *what would somebody actually say*; never worth having in a
    regression suite, where the same question twice must mean the same thing.

    **The voice is injected**, for the same reason the approver's decision
    function is: this package cannot see the agent's provider adapter, and a
    simulator that imported one would simulate one stack. The binding supplies
    `speak(brief, heard) -> said`.

    **The default model actor.** A scenario that says `kind: model` and no
    `via` gets this one, speaking through the binding's voice — in a `--live`
    run, the same upstream model the agent uses. `via: langwatch` (0.12.0)
    swaps in LangWatch Scenario's user simulator through `MODEL_ACTORS`
    instead: its own model, named by the scenario, so a scripted agent can
    meet a model-played customer. Both are `MODEL_DRIVEN`, and both are judged
    the same way — by state.
    """

    determinism = Determinism.MODEL_DRIVEN

    def __init__(self, brief: str, speak, *, max_turns: int = 6) -> None:
        self.brief = brief
        self.speak = speak
        self.max_turns = max_turns
        self.said: list[str] = []

    async def next(self, reply: str) -> str | None:
        """Async, unlike its scripted siblings, because a provider is. The runner
        awaits whatever an actor hands back, so the cheap actors stay cheap."""
        if len(self.said) >= self.max_turns:
            return None
        said = (await self.speak(self.brief, reply)).strip()
        if not said:
            return None
        self.said.append(said)
        return said


class ActorUnavailable(Exception):
    """A model-driven customer was asked for and cannot be had here: its extra
    is not installed, or it has no model. The scenario is fine; this
    environment cannot run it — `Unrunnable`, never a failure."""


MODEL_ACTORS: dict[str, Callable[..., object] | str] = {
    # Default bindings, by name and never by import: each adapter sits outside
    # the core and is loaded only when a scenario asks for it (ADOPTION.md).
    "langwatch": "agenttwin.actor_langwatch:actor",
}
"""Who else can play a model-driven customer, as `actor: {kind: model, via: …}`
names them. Without `via`, the customer is a `ModelActor` speaking through the
binding's voice. A binding can register its own (`MODEL_ACTORS["mine"] = fn`);
each is called with `situation`, `persona`, `model` and `max_turns` and hands
back something with `determinism` and `next(reply)`."""


def model_actor(via: str, *, situation: str, persona: str, model: str, max_turns: int):
    """The customer `via` names, or `ActorUnavailable` saying why not."""
    found = MODEL_ACTORS.get(via)
    if found is None:
        raise KeyError(f"no model actor {via!r} — known: {', '.join(sorted(MODEL_ACTORS))}")
    if isinstance(found, str):
        module, _, attribute = found.partition(":")
        found = getattr(importlib.import_module(module), attribute)
        MODEL_ACTORS[via] = found
    made = found(situation=situation, persona=persona, model=model, max_turns=max_turns)
    if getattr(made, "determinism", None) != Determinism.MODEL_DRIVEN:
        # Declared, never inferred: a model-played customer that labelled itself
        # anything else would let a run claim a reproducibility it does not have.
        raise TypeError(f"model actor {via!r} must declare Determinism.MODEL_DRIVEN")
    return made


@dataclass
class Transcript:
    """What was said, by whom, in order."""

    turns: list[Turn] = field(default_factory=list)

    def add(self, said: str, heard: str) -> None:
        self.turns.append(Turn(said=said, heard=heard))

    def render(self) -> str:
        out = []
        for i, turn in enumerate(self.turns, 1):
            out.append(f"  {i}. customer: {turn.said}")
            out.append(f"     agent:    {turn.heard}")
        return "\n".join(out)


__all__ = [
    "MODEL_ACTORS",
    "ActorUnavailable",
    "Determinism",
    "ModelActor",
    "Rule",
    "ScriptedActor",
    "StateMachineActor",
    "Transcript",
    "Turn",
    "model_actor",
    "weakest",
]
