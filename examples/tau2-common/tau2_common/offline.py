"""The offline comparison, for any τ² domain: no model is called.

For each task, the expected actions (gold) and the domain's mutations of them
are each judged by τ²'s evaluators and by AgentTwin. Writes
`results/offline.json` (every trajectory, both verdicts, AgentTwin's incidents,
the fidelity diff) and `results/offline.md` (the tables the README quotes).

Every disagreement is looked up in the domain's causes (`explain`); one with
no cause is printed as UNEXPLAINED and fails the run, so nothing is explained
after the fact.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tau2_common.judge import Bench, agenttwin, differences, tau2
from tau2_common.mutate import Variant

Explain = Callable[[str, str, list[dict]], tuple[str, str] | None]


@dataclass
class Domain:
    """What a domain gives the comparison."""

    out: Path
    explain: Explain
    own_reason: dict[str, tuple[str, ...]]
    """Mutation → incident-root fragments that say AgentTwin saw the mutation
    itself. A failure for any other reason (a spec gap's false positive
    elsewhere in the trajectory) is an agreement for the wrong reason, and is
    not counted as a catch."""
    load: Callable[[], tuple[Bench, list[Any], Callable[[Bench, Any, dict], list[Variant]]]] | None = None
    """Loads the bench, the tasks and the mutations; not needed to re-tabulate."""


def caught(r: dict, own_reason: dict[str, tuple[str, ...]]) -> bool:
    marks = own_reason.get(r["variant"], ())
    return any(m in i["root"] for i in r["agenttwin"]["incidents"] for m in marks)


async def judge(domain: Domain, bench: Bench, task: Any, session: dict, variant: Variant) -> dict:
    ours = await agenttwin(bench, task, session, variant.steps)
    theirs = tau2(bench, task, variant.steps)
    diff = differences(ours.answers, theirs.answers)
    t2, at = theirs.passed, ours.passed
    direction = "agree" if t2 == at else ("t2-only" if at else "at-only")
    why = domain.explain(variant.name, direction, ours.incidents) if direction != "agree" else None
    return {
        "task": task.id,
        "session": session,
        "variant": variant.name,
        "what": variant.what,
        "note": variant.note,
        "calls": sum(1 for s in variant.steps if s[0] in ("call", "user_call")),
        "agent_calls": sum(1 for s in variant.steps if s[0] == "call"),
        "tau2": {
            "passed": t2,
            "reward": theirs.reward,
            "db": theirs.db,
            "communicate": theirs.communicate,
            "parts": theirs.parts,
            "unjudged": theirs.unjudged,
        },
        "agenttwin": {"passed": at, "incidents": ours.incidents},
        "direction": direction,
        "why": list(why) if why else None,
        "fidelity_differences": diff,
    }


def _mark(passed: bool) -> str:
    return "pass" if passed else "FAIL"


def report(results: list[dict], own_reason: dict[str, tuple[str, ...]], title: str) -> str:
    lines = [f"# Offline comparison: τ² vs AgentTwin — {title}", ""]
    names = list(dict.fromkeys(r["variant"] for r in results))
    lines += ["## By mutation", "",
              "| Mutation | What is wrong | Trajectories | τ² fails | AgentTwin fails | AgentTwin fails for the mutation's own reason | Agree | Disagree |",
              "|---|---|---|---|---|---|---|---|"]
    for name in names:
        rs = [r for r in results if r["variant"] == name]
        lines.append(
            f"| {name} | {rs[0]['what']} | {len(rs)} | {sum(not r['tau2']['passed'] for r in rs)} | "
            f"{sum(not r['agenttwin']['passed'] for r in rs)} | {sum(caught(r, own_reason) for r in rs)} | "
            f"{sum(r['direction'] == 'agree' for r in rs)} | "
            f"{sum(r['direction'] != 'agree' for r in rs)} |"
        )
    lines += ["", "## Disagreements, by cause", "", "| Cause | Mutation | Direction | Count | Why |", "|---|---|---|---|---|"]
    grouped: dict[tuple, int] = Counter(
        (r["why"][0] if r["why"] else "UNEXPLAINED", r["variant"], r["direction"], r["why"][1] if r["why"] else "")
        for r in results if r["direction"] != "agree"
    )
    for (cause, name, direction, why), n in sorted(grouped.items()):
        lines.append(f"| {cause} | {name} | {direction} | {n} | {why} |")
    unjudged = [r for r in results if r["tau2"].get("unjudged")]
    if unjudged:
        lines += ["", "## τ²'s model-judged part, not run", "",
                  f"{len(unjudged)} trajectories, over {len({r['task'] for r in unjudged})} tasks, have "
                  "`NL_ASSERTION` in their basis with assertions a model judges. Offline, no model is called: "
                  "their τ² verdict is the rest of the basis. Every mutation here changes calls, never words, "
                  "so a model judge would see the same reply in the gold and the mutation."]
    lines += ["", "## Per task", "",
              "Each cell is τ² / AgentTwin. `—`: the mutation had no target in this customer's rows.", "",
              "| Task | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    by_task: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in results:
        by_task[r["task"]][r["variant"]] = r
    for task in by_task:  # in the order the tasks ran
        cells = []
        for name in names:
            r = by_task[task].get(name)
            if r is None:
                cells.append("—")
                continue
            cell = f"{_mark(r['tau2']['passed'])} / {_mark(r['agenttwin']['passed'])}"
            cells.append(f"**{cell}**" if r["direction"] != "agree" else cell)
        lines.append(f"| {task} | " + " | ".join(cells) + " |")
    fidelity = [r for r in results if r["fidelity_differences"]]
    calls = sum(r["calls"] for r in results)
    agent = sum(r.get("agent_calls", r["calls"]) for r in results)
    lines += ["", "## Fidelity", "",
              f"{calls} calls in {len(results)} trajectories; the stand-in's answer differed from τ²'s "
              f"environment's on {sum(len(r['fidelity_differences']) for r in fidelity)} of them."]
    if agent != calls:
        lines.append(f"{agent} of the calls were the agent's, served over the stand-in's surface. The other "
                     f"{calls - agent} were the customer's own tools, which both sides run on τ²'s environment "
                     "directly, so their agreement checks nothing AgentTwin put in the path.")
    rules = Counter(i["root"].split(":")[0] + " " + i["rule"] for r in results for i in r["agenttwin"]["incidents"])
    lines += ["", "## AgentTwin incidents, by rule and operation", "", "| Rule | Operation | Count |", "|---|---|---|"]
    for key, n in sorted(rules.items()):
        op, rule = key.split(" ")
        lines.append(f"| {rule} | {op} | {n} |")
    return "\n".join(lines) + "\n"


def _write(domain: Domain, results: list[dict], title: str) -> list[dict]:
    domain.out.mkdir(exist_ok=True)
    (domain.out / "offline.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    (domain.out / "offline.md").write_text(report(results, domain.own_reason, title))
    return [r for r in results if r["direction"] != "agree" and not r["why"]]


def rereport(domain: Domain, title: str) -> int:
    """Explain and tabulate an existing offline.json again, without judging."""
    results = json.loads((domain.out / "offline.json").read_text())
    for r in results:
        why = domain.explain(r["variant"], r["direction"], r["agenttwin"]["incidents"]) if r["direction"] != "agree" else None
        r["why"] = list(why) if why else None
    unexplained = _write(domain, results, title)
    for r in unexplained:
        print("UNEXPLAINED", r["task"], r["variant"], r["direction"], [i["root"] for i in r["agenttwin"]["incidents"]])
    print(f"{len(results)} trajectories; {len(unexplained)} unexplained")
    return 1 if unexplained else 0


async def run(domain: Domain, ids: list[str], title: str) -> int:
    if ids == ["--report"]:
        return rereport(domain, title)
    # `@file`: task ids one per line (telecom's ids hold brackets and pipes).
    ids = [i for a in ids for i in (Path(a[1:]).read_text().split("\n") if a.startswith("@") else [a]) if i]
    started = time.time()
    assert domain.load is not None
    bench, tasks, variants = domain.load()
    tasks = [t for t in tasks if not ids or t.id in ids]
    results = []
    for task in tasks:
        session = bench.session(task)
        for variant in variants(bench, task, session):
            r = await judge(domain, bench, task, session, variant)
            results.append(r)
            flag = "" if r["direction"] == "agree" else f"  <- {r['direction']} {r['why'][0] if r['why'] else 'UNEXPLAINED'}"
            print(f"task {str(task.id)[:40]:>40} {variant.name:<30} τ² {_mark(r['tau2']['passed'])}  AgentTwin "
                  f"{_mark(r['agenttwin']['passed'])}{flag}", flush=True)
    unexplained = _write(domain, results, title)
    print(f"{len(results)} trajectories in {time.time() - started:.0f}s; {len(unexplained)} unexplained")
    return 1 if unexplained else 0


def main(domain: Domain, argv: list[str], title: str) -> int:
    return asyncio.run(run(domain, argv, title))


__all__ = ["Domain", "caught", "judge", "main", "report", "rereport", "run"]
