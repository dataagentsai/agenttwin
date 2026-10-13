"""A few live tasks: τ²'s own agent under test, its tools served by the stand-in.

The agent is τ²'s `llm_agent` and the customer τ²'s `user_simulator`, built by
τ²'s own builders and run by τ²'s own orchestrator. One thing is swapped: the
environment's `get_response`, which now sends each tool call over MCP to the
AgentTwin stand-in (`standin.py`), sharing one τ² environment with it. So the
agent's every tool answer came through AgentTwin, in τ²'s words.

Judged twice: τ²'s verdict is the one its runner attached (`reward_info`),
and recomputed by `judge.tau2` from the same trajectory; AgentTwin's is its
monitor over the conversation as it happened (every call and every reply, in
order), replayed deterministically through a fresh stand-in.

Model calls go to Groq's free tier through LiteLLM, paced to stay under the
per-minute token limit, with no retries: the first rate-limit refusal stops
the run and is recorded, so the daily quota is never spent on retries.

    GROQ_API_KEY=... ./run.sh -m tau2_airline.live 0 1 5
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from mcp.client import Client

from agenttwin import SESSION_META, Live
from tau2.data_model.message import AssistantMessage, ToolMessage, UserMessage
from tau2.domains.airline.environment import get_tasks
from tau2.orchestrator.orchestrator import Orchestrator
from tau2.runner.build import build_agent, build_user
from tau2.runner.simulation import run_simulation
import tau2.utils.llm_utils as llm_utils
from tau2_airline.judge import HERE, Bench, Step, agenttwin, differences, session_user, tau2
from tau2_airline.standin import Standin

AGENT_MODEL = os.environ.get("AGENT_MODEL", "groq/openai/gpt-oss-20b")
USER_MODEL = os.environ.get("USER_MODEL", "groq/qwen/qwen3.8-27b")
TOKENS_PER_MINUTE = int(os.environ.get("TOKENS_PER_MINUTE", "8000"))
"""Each model's limit on this key's free tier (read from Groq's
`x-ratelimit-limit-tokens`, 13 Oct 2026). The agent and the customer use
different models, so each has its own minute."""
OUT = HERE / "results"


class RateLimited(Exception):
    pass


class Pace:
    """Sleeps before a model call while the last minute's tokens are at the budget."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.spent: list[tuple[float, str, int]] = []
        self.calls = 0
        self.tokens = 0
        self.limited: str | None = None
        """Set at the first rate-limit refusal: τ²'s orchestrator catches the
        agent's exceptions and ends the conversation, so it cannot propagate."""

    def __call__(self, completion):
        def paced(*args: Any, **kwargs: Any):
            while True:
                now, model = time.time(), str(kwargs.get("model", args[0] if args else ""))
                self.spent = [(t, m, n) for t, m, n in self.spent if now - t < 60]
                if sum(n for _, m, n in self.spent if m == model) < self.per_minute * 0.5:
                    break
                time.sleep(2)
            kwargs["num_retries"] = 0
            try:
                response = completion(*args, **kwargs)
            except Exception as e:
                if "rate" in str(e).lower() and "limit" in str(e).lower() or "429" in str(e):
                    self.limited = str(e)[:500]
                    raise RateLimited(self.limited) from e
                raise
            used = getattr(getattr(response, "usage", None), "total_tokens", 0) or 0
            self.spent.append((time.time(), model, used))
            self.calls += 1
            self.tokens += used
            return response

        return paced


class Wire:
    """An MCP client to the stand-in for τ²'s synchronous orchestrator: one
    coroutine on its own loop holds the client open and answers calls from a
    queue, because the client must be left in the task that entered it."""

    def __init__(self, server, user_id: str) -> None:
        self.session = {"user_id": user_id}
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.ready = threading.Event()
        self.served = asyncio.run_coroutine_threadsafe(self._serve(server), self.loop)
        self.ready.wait()

    async def _serve(self, server) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        async with Client(server) as client:
            self.ready.set()
            while (item := await self.queue.get()) is not None:
                name, arguments, done = item
                try:
                    done.set_result(await client.call_tool(name, arguments, meta={SESSION_META: self.session}))
                except Exception as e:  # handed back to the caller's thread
                    done.set_exception(e)

    def call(self, name: str, arguments: dict) -> tuple[bool, str]:
        done: concurrent.futures.Future = concurrent.futures.Future()
        self.loop.call_soon_threadsafe(self.queue.put_nowait, (name, arguments, done))
        result = done.result()
        return bool(result.is_error), result.content[0].text if result.content else ""

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.queue.put_nowait, None)
        self.served.result()
        self.loop.call_soon_threadsafe(self.loop.stop)


def steps_of(messages: list[Any]) -> list[Step]:
    """The conversation as AgentTwin's trajectory: calls, and replies with the
    customer words they answered."""
    steps: list[Step] = []
    said = ""
    for m in messages:
        if isinstance(m, UserMessage) and m.content:
            said = m.content
        elif isinstance(m, AssistantMessage):
            for call in m.tool_calls or []:
                steps.append(("call", call.name, call.arguments))
            if m.content:
                steps.append(("say", said, m.content))
                said = ""
    return steps


def run_one(bench: Bench, task, pace: Pace) -> dict:
    user_id = session_user(task)
    env = bench.env()
    standin = Standin(Live.start(bench.world), env)
    wire = Wire(standin.server(), user_id)
    served: list[tuple[bool, str]] = []

    def get_response(call):
        error, text = wire.call(call.name, call.arguments)
        served.append((error, text))
        return ToolMessage(id=call.id, content=text, requestor=call.requestor, role="tool", error=error)

    env.get_response = get_response  # the one swap: every tool call goes to the stand-in
    args = {"temperature": 0.0}
    agent = build_agent("llm_agent", env, llm=AGENT_MODEL, llm_args=args, task=task)
    user = build_user("user_simulator", env, task, llm=USER_MODEL, llm_args=args)
    started = time.time()
    try:
        simulation = run_simulation(
            Orchestrator(domain="airline", agent=agent, user=user, environment=env, task=task, max_steps=60, max_errors=3)
        )
    finally:
        wire.close()
    if pace.limited:
        raise RateLimited(pace.limited)
    steps = steps_of(simulation.messages)
    ours = asyncio.run(agenttwin(bench, user_id, steps))
    recomputed = tau2(bench, task, steps)
    return {
        "task": task.id,
        "user_id": user_id,
        "agent_model": AGENT_MODEL,
        "user_model": USER_MODEL,
        "seconds": round(time.time() - started),
        "termination": simulation.termination_reason.value,
        "tau2": {
            "reward": simulation.reward_info.reward if simulation.reward_info else None,
            "recomputed": recomputed.reward,
            "db": recomputed.db,
            "communicate": recomputed.communicate,
        },
        "agenttwin": {"passed": ours.passed, "incidents": ours.incidents},
        "served_vs_replayed": differences(served, ours.answers),
        "transcript": [list(s) for s in steps],
    }


def main(ids: list[str]) -> int:
    if not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY is not set", file=sys.stderr)
        return 2
    pace = Pace(TOKENS_PER_MINUTE)
    llm_utils.completion = pace(llm_utils.completion)
    bench = Bench()
    tasks = {t.id: t for t in get_tasks(None)}
    OUT.mkdir(exist_ok=True)
    path = OUT / "live.json"
    results: list[dict] = json.loads(path.read_text()) if path.exists() else []
    stopped = None
    for task_id in ids:
        try:
            r = run_one(bench, tasks[task_id], pace)
        except RateLimited as e:
            stopped = {"task": task_id, "rate_limited": str(e), "calls": pace.calls, "tokens": pace.tokens}
            print(f"task {task_id}: rate limited after {pace.calls} model calls — stopping", flush=True)
            break
        results = [x for x in results if x.get("task") != task_id or x.get("agent_model") != AGENT_MODEL] + [r]
        path.write_text(json.dumps(results, indent=1, ensure_ascii=False))
        print(f"task {task_id}: {r['termination']}, τ² {r['tau2']['reward']} (recomputed {r['tau2']['recomputed']}), "
              f"AgentTwin {'pass' if r['agenttwin']['passed'] else 'FAIL'} {[i['root'] for i in r['agenttwin']['incidents']]}, "
              f"{r['seconds']}s, model calls so far {pace.calls}, tokens {pace.tokens}", flush=True)
    if stopped:
        (OUT / "live-stopped.json").write_text(json.dumps(stopped, indent=1))
    print(f"model calls {pace.calls}, tokens {pace.tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
