"""The provider twin on Anthropic's Messages wire: the same script and faults as
test_provider.py, answered in Anthropic's shapes."""

from __future__ import annotations

import httpx2
import pytest

from agenttwin import ModelTurnFile, ProviderTwin
from tests.test_provider import CALL, CASES, SAY, Tick

# ---- the Anthropic Messages wire: same script, same faults, Anthropic's shapes ----


def messages_kind(body: dict) -> str:
    if body.get("type") == "error":
        return "error"
    if not body.get("content"):
        return "empty"
    return "call" if any(b["type"] == "tool_use" for b in body["content"]) else "text"


# Same rows as CASES, with Anthropic's outage status (529) where it differs.
MESSAGES_CASES = [
    (
        name,
        script,
        faults,
        [
            (529 if (s == 503 and faults and faults[0][1] == "provider_unavailable") else s, k)
            for s, k in expected
        ],
    )
    for name, script, faults, expected in CASES
]


@pytest.mark.parametrize(
    "name,script,faults,expected", MESSAGES_CASES, ids=[c[0] for c in MESSAGES_CASES]
)
async def test_the_messages_wire_answers_as_declared(name, script, faults, expected) -> None:
    async with ProviderTwin(script=script, faults=faults, clock=Tick()) as twin:
        seen = []
        async with httpx2.AsyncClient() as client:
            for _ in expected:
                answer = await client.post(
                    f"{twin.endpoint.anthropic_base_url}/v1/messages",
                    json={
                        "model": "m",
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
                seen.append((answer.status_code, messages_kind(answer.json())))
    assert seen == expected


# [name, scripted turn, expected content block types, stop_reason]
SHAPES = [
    ("words", SAY, ["text"], "end_turn"),
    ("a call", CALL, ["tool_use"], "tool_use"),
    (
        "words and a call",
        ModelTurnFile(says="checking", calls=({"renew": {"id": "L-1"}},)),
        ["text", "tool_use"],
        "tool_use",
    ),
]


@pytest.mark.parametrize("name,turn,blocks,stop", SHAPES, ids=[s[0] for s in SHAPES])
async def test_a_scripted_turn_arrives_in_anthropics_shape(name, turn, blocks, stop) -> None:
    async with ProviderTwin(script=(turn,)) as twin, httpx2.AsyncClient() as client:
        body = (
            await client.post(f"{twin.endpoint.anthropic_base_url}/v1/messages", json={})
        ).json()
    assert [b["type"] for b in body["content"]] == blocks
    assert body["stop_reason"] == stop
    for block in body["content"]:
        if block["type"] == "tool_use":
            assert (block["name"], block["input"]) == ("renew", {"id": "L-1"})
            assert block["id"].startswith("toolu_")


async def test_a_messages_throttle_is_anthropics_rate_limit_error() -> None:
    faults = ((1, "provider_throttled", 7.0, 0),)
    async with ProviderTwin(script=(SAY,), faults=faults) as twin, httpx2.AsyncClient() as client:
        answer = await client.post(f"{twin.endpoint.anthropic_base_url}/v1/messages", json={})
    assert answer.status_code == 429
    assert answer.headers["retry-after"] == "7.0"
    assert answer.json()["error"]["type"] == "rate_limit_error"


async def test_the_anthropic_sdk_drives_the_twin() -> None:
    anthropic = pytest.importorskip("anthropic")
    async with ProviderTwin(script=(CALL, SAY)) as twin:
        client = anthropic.AsyncAnthropic(
            base_url=twin.endpoint.anthropic_base_url, api_key="agenttwin", max_retries=0
        )
        first = await client.messages.create(
            model=twin.endpoint.model,
            max_tokens=10,
            messages=[{"role": "user", "content": "renew L-1"}],
        )
        second = await client.messages.create(
            model=twin.endpoint.model,
            max_tokens=10,
            messages=[{"role": "user", "content": "thanks"}],
        )
    assert first.content[0].type == "tool_use" and first.content[0].input == {"id": "L-1"}
    assert second.content[0].text == "hello"


async def test_a_call_on_the_wire_the_upstream_does_not_speak_is_refused() -> None:
    from agenttwin import Upstream

    upstream = Upstream(base_url="http://unused", api_key="k", model="m", wire="openai")
    async with ProviderTwin(upstream=upstream) as twin, httpx2.AsyncClient() as client:
        answer = await client.post(f"{twin.endpoint.anthropic_base_url}/v1/messages", json={})
    assert answer.status_code == 400
    assert "speaks openai" in answer.json()["error"]["message"]
