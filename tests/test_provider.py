"""The provider twin, spoken to over the wire the way any implementation would.

Table-driven: each row is a script and a set of declared faults, and the
answers a caller sees on successive calls — status code, and what the body
carries.
"""

from __future__ import annotations

import json

import httpx2
import pytest

from agenttwin import ModelTurnFile, ProviderTwin, load_scenario
from agenttwin.scenario_file import InvalidScenario

SAY = ModelTurnFile(says="hello")
CALL = ModelTurnFile(calls=({"renew": {"id": "L-1"}},))


class Tick:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# [name, script, faults, expected per call: (status, "text"|"call"|"empty"|"error")]
CASES = [
    ("a script is served in order", (CALL, SAY), (), [(200, "call"), (200, "text")]),
    ("an empty script refuses the first call", (), (), [(503, "error")]),
    ("a call past the script overruns", (SAY,), (), [(200, "text"), (503, "error")]),
    (
        "a throttle is a 429 and costs no answer",
        (SAY,),
        ((1, "provider_throttled", 2.0, 0),),
        [(429, "error"), (200, "text")],
    ),
    (
        "an outage is a 503",
        (SAY,),
        ((1, "provider_unavailable", None, 0),),
        [(503, "error"), (200, "text")],
    ),
    (
        "malformed output is a 200 with nothing in it",
        (SAY,),
        ((1, "provider_malformed", None, 0),),
        [(200, "empty"), (200, "text")],
    ),
    (
        "a lasting outage lasts until the clock passes it",
        (SAY,),
        ((1, "provider_unavailable", None, 60),),
        [(503, "error"), (503, "error")],
    ),
]


def kind_of(body: dict) -> str:
    if "error" in body:
        return "error"
    if not body.get("choices"):
        return "empty"
    message = body["choices"][0]["message"]
    return "call" if message.get("tool_calls") else "text"


@pytest.mark.parametrize("name,script,faults,expected", CASES, ids=[c[0] for c in CASES])
async def test_the_twin_answers_as_declared(name, script, faults, expected) -> None:
    async with ProviderTwin(script=script, faults=faults, clock=Tick()) as twin:
        seen = []
        async with httpx2.AsyncClient() as client:
            for _ in expected:
                answer = await client.post(
                    f"{twin.endpoint.base_url}/chat/completions",
                    json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                )
                seen.append((answer.status_code, kind_of(answer.json())))
    assert seen == expected
    assert twin.unfired == ()


async def test_a_throttle_carries_the_providers_retry_after() -> None:
    faults = ((1, "provider_throttled", 7.0, 0),)
    async with ProviderTwin(script=(SAY,), faults=faults) as twin, httpx2.AsyncClient() as client:
        answer = await client.post(f"{twin.endpoint.base_url}/chat/completions", json={})
    assert answer.headers["retry-after"] == "7.0"


async def test_a_scripted_call_arrives_as_an_openai_tool_call() -> None:
    async with ProviderTwin(script=(CALL,)) as twin, httpx2.AsyncClient() as client:
        body = (await client.post(f"{twin.endpoint.base_url}/chat/completions", json={})).json()
    call = body["choices"][0]["message"]["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "renew"
    assert json.loads(call["function"]["arguments"]) == {"id": "L-1"}
    assert body["choices"][0]["finish_reason"] == "tool_calls"


# [name, model block, error or None]
BLOCKS = [
    ("words", [{"says": "hi"}], None),
    ("a call", [{"calls": [{"renew": {"id": "L-1"}}]}], None),
    ("repeated", [{"says": "hi", "times": 3}], None),
    ("neither words nor calls", [{"times": 2}], "says something, calls something"),
    ("two operations in one call", [{"calls": [{"a": {}, "b": {}}]}], "exactly one operation"),
    ("zero times", [{"says": "hi", "times": 0}], "greater than or equal to 1"),
]


@pytest.mark.parametrize("name,block,error", BLOCKS, ids=[b[0] for b in BLOCKS])
def test_a_model_block_loads_or_says_why(tmp_path, name, block, error) -> None:
    import yaml

    from tests.test_loader import SPEC, WORLD, write

    write(tmp_path, SPEC, WORLD)
    path = tmp_path / "s.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "awd-scenario/v0",
                "scenario": name,
                "world": "branch.world.yaml",
                "as": "M-1",
                "model": block,
                "expect": [{"world": "unchanged"}],
            }
        )
    )
    if error is None:
        scenario = load_scenario(path)
        assert len(scenario.scripted_answers()) == sum(t.get("times", 1) for t in block)
    else:
        with pytest.raises(InvalidScenario, match=error):
            load_scenario(path)
