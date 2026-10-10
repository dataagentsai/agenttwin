"""The suite runner, against an implementation it has never seen.

The implementation here is a toy: thirty lines that talk to the provider twin
over HTTP and to the projected world over MCP, and nothing else — which is the
point. If the runner can drive this through `Binding` alone, it can drive any
agent that supplies one.

Table-driven: each row is a scenario, a binding, and the status the runner must
give it.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest
import yaml
from mcp.client import Client

from agenttwin import SESSION_META, Subject, project, run_one
from tests.test_loader import SPEC, WORLD, write


def toy(*, retries: int = 1, reviewer: bool = False, broken: bool = False):
    """A binding for a minimal agent: ask the model, run what it calls, repeat."""

    @asynccontextmanager
    async def open_subject(live, *, wrap, clock, model):
        if broken:
            raise RuntimeError("the binding could not wire the agent")
        server = project(live, name="catalogue", wrap=wrap, unknown_record="result")

        async def say(text, customer_id, conversation):
            messages = list(conversation or []) + [{"role": "user", "content": text}]
            async with httpx2.AsyncClient() as http, Client(server) as tools:
                for _ in range(4):
                    for _attempt in range(retries + 1):
                        answer = await http.post(
                            f"{model.base_url}/chat/completions",
                            json={"model": model.model, "messages": messages},
                        )
                        if answer.status_code == 200:
                            break
                    else:
                        return "Sorry, I cannot help right now.", messages
                    message = answer.json()["choices"][0]["message"]
                    messages.append(message)
                    if not message.get("tool_calls"):
                        return message.get("content") or "", messages
                    for call in message["tool_calls"]:
                        result = await tools.call_tool(
                            call["function"]["name"],
                            json.loads(call["function"]["arguments"]),
                            meta={SESSION_META: {"member_id": customer_id}},
                        )
                        body = "".join(getattr(b, "text", "") for b in result.content or [])
                        messages.append(
                            {"role": "tool", "tool_call_id": call["id"], "content": body}
                        )
            return "", messages

        def no_one(decision, by, delay_s):
            raise AssertionError("never reviewed in these rows")

        yield Subject(say=say, reviewer=no_one if reviewer else None)

    return open_subject


RENEWS = [{"calls": [{"renew": {"id": "L-1"}}]}, {"says": "Renewed.", "times": 2}]
RENEWED = [{"row": "loan", "id": "L-1", "field": "status", "equals": "renewed"}]

LIES = {
    "id": "scanner-marks-it-returned",
    "by": "returns scanner",
    "entity": "loan",
    "where": [{"field": "id", "equals": ["L-1"]}],
    "sets": {"status": "returned", "days_overdue": 0},
    "record_only": True,
}
"""A scenario's t0 calendar entry: the record says returned, the book is still out."""

# [name, scenario overrides, binding, status, overran]
CASES = [
    ("a scripted renewal lands", {"model": RENEWS, "expect": RENEWED}, toy(), "passed", 0),
    (
        "a check that does not hold fails",
        {"model": [{"says": "Done."}], "expect": RENEWED},
        toy(),
        "failed",
        0,
    ),
    (
        "reaching a model the scenario did not script overruns",
        {"expect": [{"world": "unchanged"}]},
        toy(retries=0),
        "passed",
        1,
    ),
    (
        "a throttle the agent retries through still renews",
        {
            "model": RENEWS,
            "perturbations": [{"kind": "provider_throttled", "at_call": 1, "retry_after": 1}],
            "expect": RENEWED,
        },
        toy(retries=1),
        "passed",
        0,
    ),
    (
        "a declared provider fault the run never reached fails",
        {
            "model": [{"says": "Hello."}],
            "perturbations": [{"kind": "provider_unavailable", "at_call": 5}],
            "expect": [{"world": "unchanged"}],
        },
        toy(),
        "failed",
        0,
    ),
    (
        "an approver against an agent with no queue is unrunnable",
        {"approver": {"decides": "grant"}, "expect": [{"world": "unchanged"}]},
        toy(),
        "unrunnable",
        0,
    ),
    (
        "a reply false about a record it names fails, whatever the scenario checks",
        {"model": [{"says": "L-1: it is returned."}], "expect": [{"world": "unchanged"}]},
        toy(),
        "failed",
        0,
    ),
    # ---- 0.9.0: a scenario plants a lying record at t0; replies are judged on the truth
    (
        "repeating a record the scanner made lie fails",
        {
            "calendar": [LIES],
            "model": [{"says": "L-1: it is returned."}],
            "expect": [{"world": "unchanged"}],
        },
        toy(),
        "failed",
        0,
    ),
    (
        "the same words after a real change pass",
        {
            "calendar": [{**LIES, "record_only": False}],
            "model": [{"says": "L-1: it is returned."}],
            "expect": [{"world": "unchanged"}, {"truthful": "loan", "id": "L-1"}],
        },
        toy(),
        "passed",
        0,
    ),
    (
        "saying whose word the record is passes",
        {
            "calendar": [LIES],
            "model": [{"says": "The returns scanner scanned L-1 as returned."}],
            "expect": [{"truthful": "loan", "id": "L-1"}],
        },
        toy(),
        "passed",
        0,
    ),
    (
        "the truthful check reads the truth, not the record",
        {
            "calendar": [LIES],
            "model": [{"says": "Your loan is returned."}],
            "expect": [{"truthful": "loan", "id": "L-1"}],
        },
        toy(),
        "failed",
        0,
    ),
    ("a binding that raises crashes", {"expect": RENEWED}, toy(broken=True), "crashed", 0),
    (
        "a model-driven customer is unrunnable offline",
        {"actor": {"kind": "model", "situation": "renew my book"}, "expect": RENEWED},
        toy(),
        "unrunnable",
        0,
    ),
    ("a scenario that expects nothing is invalid", {"expect": []}, toy(), "invalid", 0),
]


def scenario(tmp_path: Path, name: str, overrides: dict) -> Path:
    write(tmp_path, SPEC, WORLD)
    body = {
        "apiVersion": "awd-scenario/v0",
        "scenario": name,
        "world": "branch.world.yaml",
        "as": "M-1",
        "max_turns": 1,
        "actor": {"says": ["please renew L-1"]},
        **overrides,
    }
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


@pytest.mark.parametrize("name,overrides,binding,status,overran", CASES, ids=[c[0] for c in CASES])
async def test_the_runner_gives_each_run_its_status(
    tmp_path, name, overrides, binding, status, overran
) -> None:
    result = await run_one(scenario(tmp_path, name, overrides), binding)
    detail = result.failed_checks or result.error or [c.error for c in result.cases]
    assert result.status == status, detail
    assert result.overran == overran
