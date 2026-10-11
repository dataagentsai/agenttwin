"""A model-played customer through LangWatch Scenario's user simulator (0.12.0).

Offline throughout: the simulator's one model call (`litellm.completion`) is a
scripted fake. The live check against Groq is opt-in and separate
(`AGENTTWIN_LIVE_ACTOR=1`, `test_one_live_conversation` below), never part of
the default suite or the gates.

The rows that need LangWatch Scenario itself are skipped where the
`langwatch` extra is not installed — it is not in `dev` (about 380 MB, and
pins older `openai` than the rest of the lock). Everything about the seam —
the declaration, the refusals, the labelling — runs everywhere.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agenttwin import run_one
from agenttwin.actor import MODEL_ACTORS, Determinism, model_actor
from agenttwin.personas import PERSONAS
from agenttwin.runner import summary
from agenttwin.scenario_file import InvalidScenario, load_scenario
from tests.test_runner import RENEWED, RENEWS, scenario, toy

ROOT = Path(__file__).resolve().parents[1]


def _installed() -> bool:
    try:
        return importlib.util.find_spec("scenario") is not None and bool(
            importlib.util.find_spec("litellm")
        )
    except (ImportError, ValueError):
        return False


needs_extra = pytest.mark.skipif(
    not _installed(), reason="the langwatch extra is not installed (uv add 'agenttwin[langwatch]')"
)

LANGWATCH = {"kind": "model", "via": "langwatch", "model": "groq/fake", "situation": "renew L-1"}


# ----------------------------------------------------------- the declaration

# [name, actor block, error fragment or None]
DECLARATIONS = [
    ("via langwatch with a model loads", LANGWATCH, None),
    (
        "via without a model loads (the model may come from the environment)",
        {"kind": "model", "via": "langwatch"},
        None,
    ),
    ("via on a scripted actor is refused", {"via": "langwatch", "says": ["hi"]}, "kind is"),
    (
        "a model without via is refused",
        {"kind": "model", "model": "groq/x"},
        "without `via`",
    ),
    ("an unknown via is refused", {"kind": "model", "via": "nobody"}, "no model actor"),
]


@pytest.mark.parametrize("name,actor,error", DECLARATIONS, ids=[d[0] for d in DECLARATIONS])
def test_a_via_declaration_is_checked_at_load(tmp_path, name, actor, error) -> None:
    path = scenario(tmp_path, name, {"actor": actor, "expect": RENEWED})
    if error is None:
        assert load_scenario(path).actor.via == actor["via"]
        return
    with pytest.raises(InvalidScenario, match=error):
        load_scenario(path)


# ------------------------------------------------------- the clear refusals


@pytest.fixture
def without_the_extra(monkeypatch):
    """As if LangWatch Scenario were not installed, whether or not it is."""
    monkeypatch.setitem(sys.modules, "scenario", None)
    monkeypatch.delitem(sys.modules, "agenttwin.actor_langwatch", raising=False)
    monkeypatch.setitem(MODEL_ACTORS, "langwatch", "agenttwin.actor_langwatch:actor")


# [name, actor block, environment model, error fragment]
REFUSALS = [
    ("without the extra it says how to install it", LANGWATCH, "", "agenttwin[langwatch]"),
    (
        "without a model it says where to name one",
        {**LANGWATCH, "model": ""},
        "",
        "needs a model",
    ),
]


@pytest.mark.parametrize("name,actor,env_model,error", REFUSALS, ids=[r[0] for r in REFUSALS])
async def test_a_customer_that_cannot_be_had_is_unrunnable(
    tmp_path, monkeypatch, without_the_extra, name, actor, env_model, error
) -> None:
    monkeypatch.delenv("AGENTTWIN_ACTOR_MODEL", raising=False)
    path = scenario(tmp_path, name, {"actor": actor, "model": RENEWS, "expect": RENEWED})
    result = await run_one(path, toy())
    assert result.status == "unrunnable", result.cases
    assert error in result.cases[0].error


# ----------------------------------------------------------- the seam itself


class _Labelled:
    def __init__(self, determinism):
        self.determinism = determinism

    async def next(self, reply):
        return None


# [name, registered factory, error or None]
SEAM = [
    ("a model-driven actor is accepted", lambda **kw: _Labelled(Determinism.MODEL_DRIVEN), None),
    (
        "an actor claiming to be reproducible is refused",
        lambda **kw: _Labelled(Determinism.SCRIPTED),
        TypeError,
    ),
    ("an actor declaring nothing is refused", lambda **kw: object(), TypeError),
]


@pytest.mark.parametrize("name,factory,error", SEAM, ids=[s[0] for s in SEAM])
def test_a_model_actor_must_declare_itself_model_driven(monkeypatch, name, factory, error) -> None:
    monkeypatch.setitem(MODEL_ACTORS, "mine", factory)
    made = lambda: model_actor(  # noqa: E731
        "mine", situation="s", persona="plain", model="m", max_turns=1
    )
    if error is None:
        assert made().determinism == Determinism.MODEL_DRIVEN
    else:
        with pytest.raises(error):
            made()


def test_an_unknown_model_actor_is_named() -> None:
    with pytest.raises(KeyError, match="known: langwatch"):
        model_actor("nobody", situation="s", persona="plain", model="m", max_turns=1)


# ----------------------------------------------- the adapter, on a fake model


class FakeModel:
    """The simulator's model call, scripted: answers in order, records what
    it was sent. An empty answer is the customer having nothing to say."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.sent: list[dict] = []

    def __call__(self, **kwargs):
        import litellm

        self.sent.append(kwargs)
        text = self.answers.pop(0) if self.answers else ""
        return litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": text}}])


@pytest.fixture
def fake_model(monkeypatch):
    def install(*answers: str) -> FakeModel:
        import litellm

        fake = FakeModel(*answers)
        monkeypatch.setattr(litellm, "completion", fake)
        return fake

    return install


# [name, persona, situation, agent replies, model answers, max_turns, said, model calls]
CONVERSATIONS = [
    (
        "the opening comes from the simulator",
        "plain",
        "renew my library book",
        [""],
        ["please renew L-1"],
        3,
        ["please renew L-1"],
        1,
    ),
    (
        "each reply is answered in turn",
        "impatient",
        "renew my library book",
        ["", "Which loan?"],
        ["renew my book", "L-1. how long will this take"],
        3,
        ["renew my book", "L-1. how long will this take"],
        2,
    ),
    (
        "the turn budget ends it without a call",
        "plain",
        "renew my library book",
        ["", "Done."],
        ["renew L-1", "never asked"],
        1,
        ["renew L-1", None],
        1,
    ),
    (
        "an empty answer ends the conversation",
        "plain",
        "renew my library book",
        ["", "Done."],
        ["renew L-1", "   "],
        3,
        ["renew L-1", None],
        2,
    ),
]


@needs_extra
@pytest.mark.parametrize(
    "name,persona,situation,replies,answers,max_turns,said,calls",
    CONVERSATIONS,
    ids=[c[0] for c in CONVERSATIONS],
)
async def test_the_simulator_says_the_next_thing(
    fake_model, name, persona, situation, replies, answers, max_turns, said, calls
) -> None:
    from agenttwin.actor_langwatch import LangWatchActor

    fake = fake_model(*answers)
    actor = LangWatchActor(
        situation=situation, persona=persona, model="groq/fake", max_turns=max_turns
    )
    assert actor.determinism == Determinism.MODEL_DRIVEN
    got = [await actor.next(reply) for reply in replies]
    assert got == said
    assert actor.calls == len(fake.sent) == calls
    system = fake.sent[0]["messages"][0]["content"]
    assert PERSONAS[persona] in system  # the persona, mapped from the catalogue
    assert situation in system  # the scenario's situation is LangWatch's description
    assert fake.sent[0]["model"] == "groq/fake"


@needs_extra
async def test_the_simulator_sees_the_conversation_so_far(fake_model) -> None:
    from agenttwin.actor_langwatch import LangWatchActor

    fake = fake_model("renew my book", "L-1")
    actor = LangWatchActor(situation="renew", persona="plain", model="groq/fake")
    await actor.next("")
    await actor.next("Which loan?")
    # LangWatch reverses the roles for its own call: the customer's words are
    # the model's (`assistant`), the agent's are what it answers (`user`).
    history = [(m["role"], m["content"]) for m in fake.sent[1]["messages"][1:]]
    assert history == [
        ("user", "Hello, how can I help you today?"),
        ("assistant", "renew my book"),
        ("user", "Which loan?"),
    ]


# [name, persona]
PERSONA_ROWS = [(p, p) for p in sorted(PERSONAS)]


@needs_extra
@pytest.mark.parametrize("name,persona", PERSONA_ROWS, ids=[p[0] for p in PERSONA_ROWS])
async def test_every_persona_reaches_the_simulator(fake_model, name, persona) -> None:
    from agenttwin.actor_langwatch import LangWatchActor

    fake = fake_model("hello")
    await LangWatchActor(situation="renew", persona=persona, model="groq/fake").next("")
    assert f"<persona>\n{PERSONAS[persona]}\n</persona>" in fake.sent[0]["messages"][0]["content"]


@needs_extra
def test_an_unknown_persona_is_refused() -> None:
    from agenttwin.actor_langwatch import LangWatchActor

    with pytest.raises(KeyError, match="no persona"):
        LangWatchActor(situation="renew", persona="nobody", model="groq/fake")


@needs_extra
async def test_a_run_with_a_simulated_customer_is_judged_by_state_and_labelled(
    tmp_path, fake_model
) -> None:
    """End to end on the toy agent: the simulated customer asks, the scripted
    model renews, the world is checked — and the result says it was a sample."""
    fake_model("please renew L-1", "thanks")
    overrides = {"actor": LANGWATCH, "model": RENEWS, "expect": RENEWED, "max_turns": 2}
    path = scenario(tmp_path, "renewal, customer simulated", overrides)
    result = await run_one(path, toy())
    assert result.status == "passed", result.failed_checks or result.cases
    assert result.determinism == "model_driven"
    assert [c.determinism for c in result.cases] == ["model_driven"]
    k = summary([result, result])["pass^k"]
    assert k == {path.name: {"k": 2, "passed": True}}


def test_a_scripted_run_has_no_pass_k() -> None:
    from agenttwin.runner import CaseResult, ScenarioResult

    scripted = ScenarioResult(
        file="s.yaml", scenario="s", status="passed", cases=[CaseResult("", "passed")]
    )
    assert scripted.determinism == "scripted"
    assert summary([scripted])["pass^k"] == {}


NO_NETWORK = """
import asyncio, socket

tried = []

def refuse(*a, **k):
    tried.append(a[1:2] or a[:1])
    raise OSError("network refused by the test")

socket.socket.connect = refuse
socket.socket.connect_ex = refuse
socket.getaddrinfo = refuse
socket.create_connection = refuse

from agenttwin.actor_langwatch import LangWatchActor

actor = LangWatchActor(situation="renew", persona="plain", model="groq/fake", max_turns=2)

import litellm

def fake(**kw):
    return litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": "hi"}}])

litellm.completion = fake

async def go():
    assert await actor.next("") == "hi"
    assert await actor.next("Hello") == "hi"

asyncio.run(go())
assert not tried, tried
print("offline")
"""


@needs_extra
def test_nothing_leaves_the_machine_but_the_model_call() -> None:
    """No LangWatch account, no upload, no price-map fetch: with every socket
    refused — and a LangWatch key present, which would turn their exporter on
    if anything started it — the adapter imports and converses."""
    env = {**os.environ, "LANGWATCH_API_KEY": "sk-lw-not-a-real-key"}
    env.pop("LITELLM_LOCAL_MODEL_COST_MAP", None)
    done = subprocess.run(
        [sys.executable, "-c", NO_NETWORK],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=180,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert "offline" in done.stdout


# ---------------------------------------------------- live, and only if asked

LIVE = os.environ.get("AGENTTWIN_LIVE_ACTOR") == "1"


@needs_extra
@pytest.mark.skipif(not LIVE, reason="live: set AGENTTWIN_LIVE_ACTOR=1 (and GROQ_API_KEY)")
async def test_one_live_conversation(tmp_path) -> None:
    """One real model call per turn against Groq's free tier, on the toy agent
    — the smallest live check that the simulator speaks. The demonstration
    against the reference agent is a script, not a test (README)."""
    if not os.environ.get("GROQ_API_KEY"):
        pytest.skip("GROQ_API_KEY is not set")
    actor = {
        **LANGWATCH,
        "model": os.environ.get("AGENTTWIN_ACTOR_MODEL", "groq/openai/gpt-oss-20b"),
    }
    path = scenario(tmp_path, "live renewal", {"actor": actor, "model": RENEWS, "expect": RENEWED})
    result = await run_one(path, toy())
    assert result.determinism == "model_driven"
    assert result.status in {"passed", "failed"}, result.cases
