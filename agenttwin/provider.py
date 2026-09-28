"""The provider twin — a model endpoint any implementation can be pointed at.

**Why a server and not an object.** The model provider is an external system,
and the only thing every implementation of an agent has in common with every
other is how it reaches one: over HTTP, through the OpenAI-compatible
chat-completions wire the stack declares. A scripted client written in one
agent's types drives that agent and no other — which is how the reference's
scenarios came to be runnable against exactly one implementation. A twin served
on the wire drives them all, and exercises each agent's *real* provider adapter
on the way, retries and error mapping included.

Two modes, one server:

- **scripted** — answers from the scenario's `model:` block, one per call, in
  order. Free, deterministic, and what a regression suite runs on.
- **upstream** — forwards each call to a real provider and returns its answer.
  What a live run scores as pass rates.

In both, the scenario's `provider_*` perturbations are served **as the wire
carries them**: a throttle is a 429 with `retry-after`, an outage a 503,
malformed output a 200 whose body has no choice in it. The world says what goes
wrong; the implementation's own adapter decides what that becomes.

    async with ProviderTwin(script=scenario.scripted_answers(),
                            faults=provider_faults(scenario), clock=clock) as twin:
        ...  # hand twin.endpoint to the binding

Streaming is refused (400): nothing in the family streams yet (AAC-0092 is
excluded in every AOAS so far), and a twin that half-implemented it would be
worse than one that says so.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from agenttwin.scenario_file import ModelTurnFile

SCRIPTED_MODEL = "agenttwin-scripted"


@dataclass(frozen=True)
class ModelEndpoint:
    """Where the implementation must send every model call during a run.

    `base_url` is OpenAI-compatible and ends in `/v1`. A binding that builds its
    model client from anything else is not being driven by the scenario.
    """

    base_url: str
    api_key: str = "agenttwin"
    model: str = SCRIPTED_MODEL


@dataclass(frozen=True)
class Upstream:
    """A real provider behind the twin, for live runs."""

    base_url: str
    api_key: str
    model: str
    timeout_s: float = 120.0


@dataclass
class ProviderTwin:
    script: Sequence[ModelTurnFile] = ()
    faults: Sequence[tuple[int, str, float | None, int]] = ()
    """`(call number, kind, retry_after, lasts_s)` — `suite.provider_faults`."""
    clock: Callable[[], float] | None = None
    upstream: Upstream | None = None

    calls: int = 0
    """Every chat-completions request received, faulted or not."""
    answered: int = 0
    overran: int = 0
    """Calls that arrived after the script ran out. Non-zero means the
    implementation reached the model more often than the scenario's author
    expected, so its checks may not be testing what they say."""
    fired: list[int] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    _outage: tuple[str, float | None, float] | None = None
    _server: uvicorn.Server | None = None
    _task: asyncio.Task | None = None
    _port: int = 0

    @property
    def endpoint(self) -> ModelEndpoint:
        if not self._port:
            raise RuntimeError("the twin is not serving; enter it first")
        model = self.upstream.model if self.upstream is not None else SCRIPTED_MODEL
        return ModelEndpoint(base_url=f"http://127.0.0.1:{self._port}/v1", model=model)

    @property
    def unfired(self) -> tuple[int, ...]:
        return tuple(at for at, *_ in self.faults if at not in self.fired)

    # ------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> ProviderTwin:
        app = Starlette(
            routes=[
                Route("/v1/chat/completions", self._complete, methods=["POST"]),
                Route("/chat/completions", self._complete, methods=["POST"]),
                Route("/v1/models", self._models, methods=["GET"]),
            ]
        )
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
        server = uvicorn.Server(config)
        # In-loop, so the implementation's async client and the twin share one
        # event loop and one clock. Signals stay the test runner's.
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._server = server
        self._task = asyncio.create_task(server.serve())
        while not server.started:
            if self._task.done():
                self._task.result()  # raises whatever stopped it
            await asyncio.sleep(0.005)
        self._port = server.servers[0].sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            await self._task
        self._port = 0

    # --------------------------------------------------------------- routes

    async def _models(self, _: Request) -> Response:
        name = self.upstream.model if self.upstream is not None else SCRIPTED_MODEL
        return JSONResponse({"object": "list", "data": [{"id": name, "object": "model"}]})

    async def _complete(self, request: Request) -> Response:
        body = await request.json()
        self.calls += 1
        self.requests.append(body)
        if body.get("stream"):
            return _error(400, "the provider twin does not stream", "invalid_request_error")

        fault = self._fault_now()
        if fault is not None:
            return fault
        if self.upstream is not None:
            return await self._forward(body)
        return self._scripted(body)

    def _fault_now(self) -> Response | None:
        """The declared fault on this call, or the outage one started, if any.

        A faulted call consumes no scripted answer: the answer is still owed
        when the provider comes back, exactly as with a real outage."""
        now = self.clock() if self.clock is not None else 0.0
        declared = {at: (kind, after, lasts) for at, kind, after, lasts in self.faults}
        hit = declared.get(self.calls)
        if hit is not None:
            self.fired.append(self.calls)
            kind, retry_after, lasts_s = hit
            if lasts_s:
                self._outage = (kind, retry_after, now + lasts_s)
        elif self._outage is not None and now < self._outage[2]:
            kind, retry_after, _ = self._outage
        else:
            return None
        if kind == "provider_throttled":
            headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
            return _error(429, "rate limited (injected)", "rate_limit_exceeded", headers)
        if kind == "provider_unavailable":
            return _error(503, "the provider could not be reached (injected)", "unavailable")
        # Malformed: well-formed JSON with nothing an adapter can use in it. A
        # body that is not JSON at all tests the HTTP library, not the agent.
        return JSONResponse(
            {"id": f"agenttwin-{self.calls}", "object": "chat.completion", "choices": []}
        )

    def _scripted(self, body: dict[str, Any]) -> Response:
        if self.answered >= len(self.script):
            self.overran += 1
            return _error(
                503,
                f"agenttwin: the scenario scripted {len(self.script)} model answer(s) "
                f"and this is call {self.calls}",
                "script_exhausted",
            )
        turn = self.script[self.answered]
        self.answered += 1
        return JSONResponse(completion(turn, n=self.calls, model=body.get("model", "")))

    async def _forward(self, body: dict[str, Any]) -> Response:
        import httpx2

        assert self.upstream is not None
        sent = {**body, "model": self.upstream.model}
        async with httpx2.AsyncClient(timeout=self.upstream.timeout_s) as client:
            answer = await client.post(
                f"{self.upstream.base_url.rstrip('/')}/chat/completions",
                json=sent,
                headers={"authorization": f"Bearer {self.upstream.api_key}"},
            )
        self.answered += 1
        passed = {k: v for k, v in answer.headers.items() if k.lower() == "retry-after"}
        return Response(
            answer.content,
            status_code=answer.status_code,
            headers=passed,
            media_type="application/json",
        )


def completion(turn: ModelTurnFile, *, n: int, model: str) -> dict[str, Any]:
    """One scripted answer as a chat-completions response body."""
    message: dict[str, Any] = {"role": "assistant", "content": turn.says or None}
    if turn.calls:
        message["tool_calls"] = [
            {
                "id": f"call_{n}_{i}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
            for i, call in enumerate(turn.calls, start=1)
            for name, arguments in call.items()
        ]
    return {
        "id": f"agenttwin-{n}",
        "object": "chat.completion",
        "created": 0,
        "model": model or SCRIPTED_MODEL,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if turn.calls else "stop",
            }
        ],
        # Nominal and fixed: a scripted run's cost is not a measurement, and a
        # figure that looked like one would be quoted.
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }


def _error(status: int, message: str, kind: str, headers: dict[str, str] | None = None) -> Response:
    return JSONResponse(
        {"error": {"message": message, "type": kind, "code": kind}},
        status_code=status,
        headers=headers,
    )


__all__ = ["SCRIPTED_MODEL", "ModelEndpoint", "ProviderTwin", "Upstream", "completion"]
