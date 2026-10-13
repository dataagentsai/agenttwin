"""The generated-attack run record: prepare the scenario copies, list the cases,
and summarise a run. Called by `run.sh`; each step is plain enough to run alone.

    python record.py prepare   --family DIR --work DIR
    python record.py cases     --work DIR            # needs agenttwin (+ pyrit)
    python record.py summarize --family DIR --work DIR --out DIR [--original FILE]

`prepare` copies the three planted-note scenarios unchanged except for two
lines: `world:` made absolute (the copy lives elsewhere) and `source:` added
under `generate:`. Seed, count, the key the payload is planted in, the
checks and the scripted model are the scenario's own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

SOURCES = ("templates", "pyrit", "agentdojo")

# (agent, the repo it runs in, the scenario file, relative to the family root)
SCENARIOS = (
    ("support", "reference-agent", "reference-agent/scenarios/planted-instructions.yaml"),
    (
        "support",
        "reference-agent",
        "reference-agent/scenarios/a-planted-note-on-a-cancellable-order.yaml",
    ),
    (
        "motor-claims",
        "claims-fnol-azure",
        "clean-ai-engineering/gates/motor-claims-fnol/scenarios/planted-in-claim-note.yaml",
    ),
)
AGENTS = {"support": "reference-agent", "motor-claims": "claims-fnol-azure"}
REPOS = ("agenttwin", "reference-agent", "claims-fnol-azure", "clean-ai-engineering")


def prepare(family: Path, work: Path) -> None:
    for agent, _repo, rel in SCENARIOS:
        src = family / rel
        text = src.read_text()
        world = re.search(r"^world: (.+)$", text, re.M)
        assert world, f"{src}: no world line"
        absolute = os.path.normpath(src.parent / world.group(1).strip())
        for source in SOURCES:
            t = re.sub(r"^world: .+$", f"world: {absolute}", text, count=1, flags=re.M)
            t, n = re.subn(
                r"^  kind: injection$", f"  kind: injection\n  source: {source}", t, flags=re.M
            )
            assert n == 1, f"{src}: expected one generate block"
            out = work / agent / f"{source}--{src.name}"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(t)


def cases(work: Path) -> None:
    """Every generated case, with a hash of its payload: the planted text
    itself is not published (some of it is a page of jailbreak), but anyone who
    regenerates it can check it is the same text."""
    from agenttwin.loader import load
    from agenttwin.scenario_file import load_scenario
    from agenttwin.suite import generated_cases

    out = {}
    for path in sorted(work.glob("*/*.yaml")):
        scenario = load_scenario(path)
        world = load(path.parent / scenario.world)
        g = scenario.generate
        out[f"{path.parent.name}/{path.name}"] = {
            "seed": g.seed,
            "count": g.count,
            "into": g.into,
            "key": g.key,
            "cases": [
                {
                    "case": c.name,
                    "origin": c.origin,
                    "goal": None
                    if c.goal is None
                    else {"kind": c.goal.kind, "action": c.goal.action, "key": c.goal.key},
                    "payload_sha256": hashlib.sha256(c.payload.encode()).hexdigest()[:16],
                    "payload_chars": len(c.payload),
                }
                for c in generated_cases(scenario, world)
            ],
        }
    (work / "cases.json").write_text(json.dumps(out, indent=1))


def _commit(repo: Path) -> dict:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True
        ).stdout.strip()

    dirty = git("status", "--porcelain", "--untracked-files=no")
    return {"commit": git("rev-parse", "--short=7", "HEAD"), "dirty": bool(dirty)}


def _bases(scenarios: list[dict]) -> dict:
    """The checks a scenario writes itself, per scenario file: the `templates`
    copy's, since a templates case adds none."""
    return {
        s["file"].partition("--")[2]: [o["check"] for o in s["cases"][0]["outcomes"]]
        for s in scenarios
        if s["file"].startswith("templates--")
    }


def _fingerprint(scenario_json: dict, base: list[str]) -> list[dict]:
    rows = []
    for c in scenario_json["cases"]:
        names = [o["check"] for o in c["outcomes"]]
        rows.append(
            {
                "case": c["case"],
                "origin": c["origin"],
                "status": c["status"],
                "checks": len(names),
                "added_checks": [n for n in names if n not in base],
                "failed": [o["check"] for o in c["outcomes"] if not o["passed"]],
                "overran": c["overran"],
            }
        )
    return rows


def _origin_family(origin: str) -> tuple[str, str]:
    """(source, the converter / AgentDojo task / template that made it)."""
    parts = [p.strip() for p in origin.split("·")]
    head = parts[0].split(" ", 1)
    if head[0] == "pyrit":
        jb = any(p.startswith("jailbreak ") for p in parts)
        return "pyrit", parts[-1] + (" + jailbreak" if jb else "")
    if head[0] == "agentdojo":
        return "agentdojo", head[1]
    return head[0], head[0]


def summarize(family: Path, work: Path, out: Path, original: Path | None) -> int:
    generated = json.loads((work / "cases.json").read_text())
    scenarios = []
    for agent in AGENTS:
        run = json.loads((work / f"{agent}.json").read_text())
        bases = _bases(run["scenarios"])
        for s in run["scenarios"]:
            source, _, name = s["file"].partition("--")
            key = f"{agent}/{s['file']}"
            gen = generated[key]
            rows = _fingerprint(s, bases[name])
            for row, g in zip(rows, gen["cases"], strict=True):
                assert row["origin"] == g["origin"], (key, row["case"])
                row["goal"] = g["goal"]
                row["payload_sha256"] = g["payload_sha256"]
            scenarios.append(
                {
                    "agent": agent,
                    "repo": AGENTS[agent],
                    "scenario": name,
                    "title": s["scenario"],
                    "source": source,
                    "seed": gen["seed"],
                    "count": gen["count"],
                    "into": gen["into"],
                    "key": gen["key"],
                    "status": s["status"],
                    "error": s["error"],
                    "held": sum(r["status"] == "passed" for r in rows),
                    "cases": rows,
                }
            )

    total = sum(len(s["cases"]) for s in scenarios)
    held = sum(s["held"] for s in scenarios)
    record = {
        "what": "generated injection cases, planted as world data, judged by state",
        "mode": "scripted (no model, no network): the scripted model obeys every planted goal",
        "binding": "evals.gate_binding:open_subject",
        "repos": {r: _commit(family / r) for r in REPOS},
        "pyrit": _pyrit_version(),
        "total": total,
        "held": held,
        "scenarios": scenarios,
    }

    comparison = None
    if original is not None and original.is_file():
        comparison = _compare(json.loads(original.read_text()), scenarios)
        record["compared_with_0.11.0"] = comparison

    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n")
    (out / "results.md").write_text(_markdown(record, comparison))
    print(f"{held}/{total} held")
    return 0 if held == total else 1


def _pyrit_version() -> str:
    try:
        from importlib.metadata import version

        return version("pyrit")
    except Exception:  # noqa: BLE001 — summarize runs without pyrit installed
        return os.environ.get("PYRIT_VERSION", "")


def _compare(original: dict, scenarios: list[dict]) -> dict:
    """Case by case against the 0.11.0 run's fingerprint: same origin, same
    added checks, same status."""
    same = differ = 0
    notes = []
    for s in scenarios:
        key = f"{s['agent']}/{s['source']}--{s['scenario']}"
        old = original["scenarios"].get(key)
        if old is None:
            notes.append(f"{key}: not in the original run")
            differ += len(s["cases"])
            continue
        for new, was in zip(s["cases"], old, strict=False):
            if (new["origin"], new["added_checks"], new["status"]) == (
                was["origin"],
                was["added_checks"],
                was["status"],
            ):
                same += 1
            else:
                differ += 1
                notes.append(f"{key} {new['case']}: was {was['origin']!r} {was['status']}, now {new['origin']!r} {new['status']}")
        if len(s["cases"]) != len(old):
            notes.append(f"{key}: {len(old)} cases then, {len(s['cases'])} now")
    return {"identical_cases": same, "differing_cases": differ, "notes": notes[:40]}


def _markdown(record: dict, comparison: dict | None) -> str:
    lines = [
        "# Generated attacks: the run record",
        "",
        f"**{record['held']} of {record['total']} held.** Mode: {record['mode']}.",
        "",
        "| Agent | Scenario | Planted in | Seed | templates | pyrit | agentdojo |",
        "|---|---|---|---|---|---|---|",
    ]
    by = {}
    for s in record["scenarios"]:
        by.setdefault((s["agent"], s["scenario"], s["into"], s["key"], s["seed"]), {})[s["source"]] = s
    for (agent, scen, into, key, seed), row in by.items():
        cells = [
            f"{row[src]['held']}/{len(row[src]['cases'])}" if src in row else "-" for src in SOURCES
        ]
        lines.append(f"| {agent} | `{scen}` | `{into}` of {key} | {seed} | " + " | ".join(cells) + " |")
    per_source = Counter()
    per_source_total = Counter()
    for s in record["scenarios"]:
        per_source[s["source"]] += s["held"]
        per_source_total[s["source"]] += len(s["cases"])
    lines.append(
        "| **all** | | | | "
        + " | ".join(f"**{per_source[s]}/{per_source_total[s]}**" for s in SOURCES)
        + " |"
    )
    lines += ["", "## What made the cases", ""]
    for src in ("pyrit", "agentdojo"):
        made = Counter(
            _origin_family(c["origin"])[1]
            for s in record["scenarios"]
            if s["source"] == src
            for c in s["cases"]
        )
        lines.append(f"**{src}** ({sum(made.values())} cases, {len(made)} distinct): "
                     + ", ".join(f"{k} ×{v}" for k, v in sorted(made.items())))
        lines.append("")
    goals = Counter(
        (c["goal"] or {}).get("kind", "none") for s in record["scenarios"] for c in s["cases"]
    )
    added = sum(len(c["added_checks"]) for s in record["scenarios"] for c in s["cases"])
    lines += [
        "**Goals** (pyrit and agentdojo cases; templates cases carry none): "
        + ", ".join(f"{k} ×{v}" for k, v in sorted(goals.items())),
        "",
        f"**Checks a case's goal added**, judged by state: {added} in all, every one passed.",
        "",
        "## The code it ran",
        "",
        "| Repository | Commit | Uncommitted tracked changes |",
        "|---|---|---|",
    ]
    for repo, c in record["repos"].items():
        lines.append(f"| {repo} | `{c['commit']}` | {'yes' if c['dirty'] else 'no'} |")
    lines += ["", f"PyRIT {record['pyrit'] or '(version not recorded)'}.", ""]
    if comparison is not None:
        lines += [
            "## Against the unpublished 0.11.0 run (11 October)",
            "",
            f"{comparison['identical_cases']} cases identical (same origin, same added checks, "
            f"same status), {comparison['differing_cases']} differing.",
            "",
        ]
        lines += [f"- {n}" for n in comparison["notes"]]
        lines.append("")
    return "\n".join(lines)


def fingerprint_original(src_dir: Path, out: Path) -> None:
    """The 0.11.0 run's outputs reduced to the same per-case fingerprint."""
    agents = {"clothing": "support", "fnol": "motor-claims"}
    record = {"from": "the 0.11.0 run's own JSON outputs, 11 October 2026", "scenarios": {}}
    for old, agent in agents.items():
        run = json.loads((src_dir / f"{old}.json").read_text())
        record["binding"] = run["binding"]
        record["mode"] = run["mode"]
        bases = _bases(run["scenarios"])
        for s in run["scenarios"]:
            base = bases[s["file"].partition("--")[2]]
            record["scenarios"][f"{agent}/{s['file']}"] = _fingerprint(s, base)
    out.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("step", choices=["prepare", "cases", "summarize", "fingerprint-original"])
    p.add_argument("--family", type=Path)
    p.add_argument("--work", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--original", type=Path)
    a = p.parse_args()
    if a.step == "prepare":
        prepare(a.family, a.work)
    elif a.step == "cases":
        cases(a.work)
    elif a.step == "summarize":
        return summarize(a.family, a.work, a.out, a.original)
    else:
        fingerprint_original(a.work, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
