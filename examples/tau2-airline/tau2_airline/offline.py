"""The offline comparison: no model is called.

For each of τ²'s 50 airline tasks, the expected actions (gold) and the
mutations of them (`mutate.py`) are each judged by τ²'s evaluator and by
AgentTwin. Writes `results/offline.json` (every trajectory, both verdicts,
AgentTwin's incidents, the fidelity diff) and `results/offline.md` (the
tables the README quotes).

    ./run.sh -m tau2_airline.offline [task ids...]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from tau2.domains.airline.environment import get_tasks
from tau2_airline.judge import HERE, Bench, agenttwin, differences, session_user, tau2
from tau2_airline.mutate import variants

OUT = HERE / "results"

# Why the two judges disagree, by mutation and direction. Every disagreement
# the run finds is looked up here; one with no entry is printed as
# UNEXPLAINED and fails the run, so nothing is explained after the fact.
WHY: dict[tuple[str, str], tuple[str, str]] = {
    ("drop-last-write", "t2-only"): (
        "by design",
        "AgentTwin judged the policy, not the request: no scenario states what this customer asked "
        "for, and leaving a request undone breaks no rule. τ² compares with the expected database",
    ),
    ("repeat-last-write", "t2-only"): (
        "AgentTwin gap G-1",
        "a second booking names no existing row, so at-most-once (keyed on the row a call names) "
        "cannot see it; the AOAS's `identity` for the booking is not carried into the world",
    ),
    ("cancel-insured-reason-unknown", "t2-only"): (
        "spec gap S-3",
        "insurance covers a cancellation only for a covered reason, and no record holds the reason",
    ),
    ("certificate-unasked", "t2-only"): (
        "spec gap S-6",
        "the customer could be compensated for something, so the certificate is within what state "
        "shows; that nobody asked, and the amount, are beyond the vocabulary",
    ),
    ("remove-bag", "t2-only"): (
        "spec gap S-12",
        "\"add, never remove\" compares an input with the row's field; the vocabulary compares a "
        "row with constants",
    ),
    ("change-basic-economy-flight", "t2-only"): (
        "spec gap S-12",
        "\"basic economy flights cannot be modified\" is about the change (old flights vs new), and "
        "a precondition or invariant sees one row at one time",
    ),
    ("read-other-customer", "at-only"): (
        "AgentTwin catches",
        "another customer's reservation was shown; nothing changed, so a database comparison "
        "cannot see it",
    ),
}


BAGS_AFTER_DOWNGRADE = "invariant:paid bags are only the bags beyond the allowance"
S16 = (
    "spec gap S-16",
    "the free-bag allowance is stated under booking; after a cabin downgrade lowers it, the "
    "policy never says whether bags already free become paid. The AOAS's invariant reads it as "
    "always; τ²'s expected actions leave the bags as they were",
)


CARDS_AFTER_CHANGE = "invariant:at most one credit card"
S17 = (
    "spec gap S-17 (a false positive of this AOAS)",
    "the payment caps are stated for booking, and a change may be paid with any card in the "
    "profile; an invariant holds of every row at every time, and the format cannot say \"of the "
    "payments made at booking\", so a second card used for a change reads as a broken cap",
)


def explain(variant: str, direction: str, incidents: list[dict]) -> tuple[str, str] | None:
    """The cause of a disagreement: by the incident that made it, where one
    explains it whatever the mutation, else by mutation and direction."""
    if direction == "agree":
        return None
    if direction == "at-only" and incidents and all(
        i["root"].startswith("update_reservation_flights:") and i["root"].endswith(BAGS_AFTER_DOWNGRADE)
        for i in incidents
    ):
        return S16
    if direction == "at-only" and incidents and all(
        i["root"].startswith("update_reservation_flights:") and i["root"].endswith(CARDS_AFTER_CHANGE)
        for i in incidents
    ):
        return S17
    return WHY.get((variant, direction))


async def judge(bench: Bench, task, user_id: str, variant) -> dict:
    ours = await agenttwin(bench, user_id, variant.steps)
    theirs = tau2(bench, task, variant.steps)
    diff = differences(ours.answers, theirs.answers)
    t2, at = theirs.passed, ours.passed
    direction = "agree" if t2 == at else ("t2-only" if at else "at-only")
    why = explain(variant.name, direction, ours.incidents)
    return {
        "task": task.id,
        "user_id": user_id,
        "variant": variant.name,
        "what": variant.what,
        "note": variant.note,
        "calls": sum(1 for s in variant.steps if s[0] == "call"),
        "tau2": {"passed": t2, "reward": theirs.reward, "db": theirs.db, "communicate": theirs.communicate},
        "agenttwin": {"passed": at, "incidents": ours.incidents},
        "direction": direction,
        "why": list(why) if why else None,
        "fidelity_differences": diff,
    }


OWN_REASON: dict[str, tuple[str, ...]] = {
    "cancel-no-ground": ("precondition:cancellation_basis",),
    "cancel-flown": ("precondition:segments_flown",),
    "cancel-other-customer": (":owner", "user_id="),
    "read-other-customer": ("user_id=",),
    "certificate-unasked": ("precondition:compensation_eligible", "precondition:disrupted_reservations"),
    "misprice-bag": ("invariant:paid bags",),
    "change-destination": ("invariant:a reservation keeps its route",),
    "repeat-last-write": ("precondition:status",),
}
"""The incident that says AgentTwin saw the mutation itself. A failure for any
other reason (a spec gap's false positive elsewhere in the trajectory) is an
agreement for the wrong reason, and is not counted as a catch."""


def caught(r: dict) -> bool:
    marks = OWN_REASON.get(r["variant"], ())
    return any(m in i["root"] for i in r["agenttwin"]["incidents"] for m in marks)


def _mark(passed: bool) -> str:
    return "pass" if passed else "FAIL"


def report(results: list[dict]) -> str:
    lines = ["# Offline comparison: τ² vs AgentTwin", ""]
    names = list(dict.fromkeys(r["variant"] for r in results))
    lines += ["## By mutation", "",
              "| Mutation | What is wrong | Trajectories | τ² fails | AgentTwin fails | AgentTwin fails for the mutation's own reason | Agree | Disagree |",
              "|---|---|---|---|---|---|---|---|"]
    for name in names:
        rs = [r for r in results if r["variant"] == name]
        lines.append(
            f"| {name} | {rs[0]['what']} | {len(rs)} | {sum(not r['tau2']['passed'] for r in rs)} | "
            f"{sum(not r['agenttwin']['passed'] for r in rs)} | {sum(caught(r) for r in rs)} | "
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
    lines += ["", "## Per task", "",
              "Each cell is τ² / AgentTwin. `—`: the mutation had no target in this customer's rows.", "",
              "| Task | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    by_task: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in results:
        by_task[r["task"]][r["variant"]] = r
    for task in sorted(by_task, key=int):
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
    lines += ["", "## Fidelity", "",
              f"{calls} calls in {len(results)} trajectories; the stand-in's answer differed from τ²'s "
              f"environment's on {sum(len(r['fidelity_differences']) for r in fidelity)} of them."]
    rules = Counter(i["root"].split(":")[0] + " " + i["rule"] for r in results for i in r["agenttwin"]["incidents"])
    lines += ["", "## AgentTwin incidents, by rule and operation", "", "| Rule | Operation | Count |", "|---|---|---|"]
    for key, n in sorted(rules.items()):
        op, rule = key.split(" ")
        lines.append(f"| {rule} | {op} | {n} |")
    return "\n".join(lines) + "\n"


def rereport() -> int:
    """Explain and tabulate an existing offline.json again, without judging."""
    results = json.loads((OUT / "offline.json").read_text())
    for r in results:
        why = explain(r["variant"], r["direction"], r["agenttwin"]["incidents"])
        r["why"] = list(why) if why else None
    (OUT / "offline.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    (OUT / "offline.md").write_text(report(results))
    unexplained = [r for r in results if r["direction"] != "agree" and not r["why"]]
    for r in unexplained:
        print("UNEXPLAINED", r["task"], r["variant"], r["direction"], [i["root"] for i in r["agenttwin"]["incidents"]])
    print(f"{len(results)} trajectories; {len(unexplained)} unexplained")
    return 1 if unexplained else 0


async def main(ids: list[str]) -> int:
    if ids == ["--report"]:
        return rereport()
    started = time.time()
    bench = Bench()
    tasks = [t for t in get_tasks(None) if not ids or t.id in ids]
    results = []
    for task in tasks:
        user_id = session_user(task)
        for variant in variants(bench, task, user_id):
            r = await judge(bench, task, user_id, variant)
            results.append(r)
            flag = "" if r["direction"] == "agree" else f"  <- {r['direction']} {r['why'][0] if r['why'] else 'UNEXPLAINED'}"
            print(f"task {task.id:>2} {variant.name:<30} τ² {_mark(r['tau2']['passed'])}  AgentTwin "
                  f"{_mark(r['agenttwin']['passed'])}{flag}", flush=True)
    OUT.mkdir(exist_ok=True)
    (OUT / "offline.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    (OUT / "offline.md").write_text(report(results))
    unexplained = [r for r in results if r["direction"] != "agree" and not r["why"]]
    print(f"{len(results)} trajectories in {time.time() - started:.0f}s; {len(unexplained)} unexplained")
    return 1 if unexplained else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
