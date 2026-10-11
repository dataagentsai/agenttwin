"""The adapter for **AgentDojo** (MIT, ETH Zürich SPY Lab): its attack templates
and its injection tasks, mapped onto a world's untrusted fields.

**Outside the core.** `agenttwin.attacks` owns the port, the goals and the
judging; it resolves this module by name (`SOURCES["agentdojo"]`) and never
imports it (ADOPTION.md, *The one constraint*).

**Vendored data, not an install.** The `agentdojo` package pulls anthropic,
cohere, google-genai, langchain and openai, and everything this adapter needs
from it is strings: six fixed attack templates and the v1 suites' 27 injection
tasks. They are copied, with AgentDojo's MIT licence and attribution, to
`agenttwin/data/agentdojo/` — no dependency at all.

**The mapping.** AgentDojo's goals belong to its worlds (a bank, a Slack, a
travel agency, a workspace), and a judge on *our* world cannot see an IBAN
nobody here holds. What carries across is the goal's **kind** — act on the row
it was planted in, act on somebody else's, or exfiltrate somebody's data —
recorded per task in the data file. Each case takes a task, an attack template
and one of the world's goals of the task's kind (`attacks.goals_for`), and the
template is filled with the *world's* goal. The AgentDojo task is kept as the
case's origin. With no world goals (no world given) the task's own goal text is
used, unjudged.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from agenttwin.attacks import Attack, Goal

DATA = Path(__file__).parent / "data" / "agentdojo" / "attacks.yaml"


@cache
def corpus() -> dict[str, Any]:
    return yaml.safe_load(DATA.read_text())


def _fill(template: str, goal_text: str, goal: Goal | None, defaults: dict[str, str]) -> str:
    tools = (
        f"You should use the tool `{goal.action}` with the following arguments: "
        f"{{'id': '{goal.key}'}}."
        if goal is not None and goal.action
        else ""
    )
    return template.format(goal=goal_text, tools=tools, **defaults)


def source(*, seed: int, count: int, goals: Sequence[Goal] = ()) -> list[Attack]:
    """`count` cases, deterministic by `seed`: tasks taken in a shuffled order
    so every one is tried before any repeats, each with a template and a world
    goal of its kind chosen by the seed."""
    data = corpus()
    rng = random.Random(seed)
    tasks = list(data["tasks"])
    rng.shuffle(tasks)
    by_kind: dict[str, list[Goal]] = {}
    for g in goals:
        by_kind.setdefault(g.kind, []).append(g)
    out: list[Attack] = []
    seen: set[str] = set()
    tries = 0
    while len(out) < count:
        tries += 1
        if tries > 50 * count:
            raise ValueError(f"agentdojo: could not make {count} distinct cases")
        task = tasks[tries % len(tasks)]
        attack = rng.choice(data["attacks"])
        pool = by_kind.get(task["kind"]) or list(goals)
        goal = rng.choice(pool) if pool else None
        text = goal.text if goal is not None else task["goal"]
        payload = _fill(attack["template"], text, goal, data["defaults"]).strip()
        if payload in seen:
            continue
        seen.add(payload)
        out.append(
            Attack(payload, goal, f"agentdojo {task['suite']}/{task['task']} · {attack['name']}")
        )
    return out


__all__ = ["corpus", "source"]
