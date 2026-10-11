"""The adapter for **LangWatch Scenario** (Apache-2.0, LangWatch): its user
simulator plays the customer.

**Outside the core.** `agenttwin.actor` owns the seam (`MODEL_ACTORS`, the
determinism classes); a scenario selects this module by name —
`actor: {kind: model, via: langwatch}` — and the core never imports it. This
file is the only place LangWatch Scenario is named (ADOPTION.md, *The one
constraint*).

**Installed as an extra, not vendored**: `uv add 'agenttwin[langwatch]'`. It is
heavy (litellm, the LangWatch SDK, OpenTelemetry, and voice dependencies it
always installs: twilio, elevenlabs, google-genai, ffmpeg; about 380 MB), and
it pins older `openai`, `huggingface-hub` and `websockets` than the rest of the
lock, so `pyproject.toml` resolves it in its own fork. Without the extra, a
scenario declaring `via: langwatch` says so and stops.

**Only the user simulator is used.** LangWatch's judge agent is not: AgentTwin
judges by state, never by what was said, so the scenario's `expect` decides
and the simulator only decides what the customer says next. Its executor
(`scenario.run`) is not used either — the AgentTwin runner owns the loop, the
turn budget and the world.

**No cloud.** Nothing is sent anywhere but the model call:

- `scenario.run` is never called, so its tracing (`setup_scenario_tracing`,
  the OTLP exporter to app.langwatch.ai) and its event reporter are never
  started — they are what uploads, and only when `LANGWATCH_API_KEY` is set;
- litellm fetches its model-price map from GitHub on import unless
  `LITELLM_LOCAL_MODEL_COST_MAP` is set; it is set here before the import;
- LangWatch's response cache is off (no `cache_key`), so nothing is written
  to disk either.

No LangWatch account or key is needed. The model is named in litellm's form
(`groq/openai/gpt-oss-120b`) and its key comes from the provider's own
environment variable (`GROQ_API_KEY`), which litellm reads.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from agenttwin.actor import ActorUnavailable, Determinism
from agenttwin.personas import PERSONAS

INSTALL = "uv add 'agenttwin[langwatch]'"

MODEL_ENV = "AGENTTWIN_ACTOR_MODEL"
"""Where the customer's model comes from when the scenario does not say."""


def _scenario() -> Any:
    """LangWatch Scenario, imported where it is called and never at import, so
    a missing extra fails the scenario that asked for it and not the package."""
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    try:
        import scenario
        from scenario.cache import context_scenario
    except ImportError as exc:
        raise ActorUnavailable(
            f"via: langwatch needs LangWatch Scenario, which is not installed — {INSTALL}"
        ) from exc
    if not hasattr(scenario, "UserSimulatorAgent"):
        raise ActorUnavailable(
            f"via: langwatch found a `scenario` module that is not LangWatch Scenario — {INSTALL}"
        )
    return scenario, context_scenario


class LangWatchActor:
    """A customer played by LangWatch Scenario's `UserSimulatorAgent`.

    Given the situation (what the customer wants, from the scenario), the
    persona (how they behave, from `personas.PERSONAS`) and the conversation
    so far; asked once per turn for the next message. It never sees the
    world, for the same reason a state-machine actor does not.
    """

    determinism = Determinism.MODEL_DRIVEN

    def __init__(
        self,
        *,
        situation: str,
        persona: str = "plain",
        model: str = "",
        max_turns: int = 6,
        temperature: float | None = None,
    ) -> None:
        if persona not in PERSONAS:
            raise KeyError(f"no persona {persona!r} — known: {', '.join(sorted(PERSONAS))}")
        model = model or os.environ.get(MODEL_ENV, "")
        if not model:
            raise ActorUnavailable(
                f"via: langwatch needs a model — name one in the actor block "
                f"(`model: groq/openai/gpt-oss-120b`) or in {MODEL_ENV}"
            )
        self.situation = situation
        self.persona = persona
        self.model = model
        self.max_turns = max_turns
        self.temperature = temperature
        self.messages: list[dict[str, str]] = []
        """The conversation in LangWatch's terms: the customer is `user`, the
        agent `assistant`. The simulator reverses them for its own call."""
        self.calls = 0
        self._thread = f"agenttwin-{uuid.uuid4().hex[:12]}"
        self._module, self._context = _scenario()
        self._simulator = self._module.UserSimulatorAgent(
            model=model,
            persona=PERSONAS[persona],
            modality="text",
            **({} if temperature is None else {"temperature": temperature}),
        )

    @property
    def said(self) -> list[str]:
        return [m["content"] for m in self.messages if m["role"] == "user"]

    async def next(self, reply: str) -> str | None:
        if reply:
            self.messages.append({"role": "assistant", "content": reply})
        if len(self.said) >= self.max_turns:
            return None
        # litellm's completion is synchronous; a thread keeps the event loop —
        # the agent, the provider twin, the projected world — free meanwhile.
        message = await asyncio.to_thread(self._ask)
        said = _text(message).strip()
        if not said:
            return None
        self.messages.append({"role": "user", "content": said})
        return said

    def _ask(self) -> Any:
        """One simulator call, outside LangWatch's executor. The simulator's
        cache decorator reads the running scenario from a context variable;
        it is given a config with no `cache_key`, which is LangWatch's own
        "do not cache"."""
        sc = self._module
        config = sc.ScenarioConfig(max_turns=self.max_turns, verbose=False)
        state = sc.ScenarioState(
            description=self.situation,
            # The simulator reads the history from `AgentInput.messages`; the
            # state's copy is the executor's, with trace ids nobody has here.
            messages=[],
            thread_id=self._thread,
            current_turn=len(self.said),
            config=config,
        )
        given = sc.AgentInput(
            thread_id=self._thread,
            messages=list(self.messages),
            new_messages=self.messages[-1:],
            scenario_state=state,
        )
        token = self._context.set(_Running(config))
        try:
            self.calls += 1
            return asyncio.run(self._simulator.call(given))
        finally:
            self._context.reset(token)


class _Running:
    """What LangWatch's cache decorator reads off the running executor."""

    def __init__(self, config: Any) -> None:
        self.config = config


def _text(message: Any) -> str:
    """The simulator answers with an OpenAI-shaped user message (or a string)."""
    if isinstance(message, str):
        return message
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, str) else ""


def actor(*, situation: str, persona: str, model: str, max_turns: int) -> LangWatchActor:
    """The factory `agenttwin.actor.MODEL_ACTORS["langwatch"]` names."""
    return LangWatchActor(situation=situation, persona=persona, model=model, max_turns=max_turns)


__all__ = ["LangWatchActor", "actor"]
