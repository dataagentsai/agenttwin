"""Run a suite of scenario files against any implementation, and say what happened.

    python -m agenttwin run --binding evals.gate_binding:open_subject scenarios/

The runner knows the binding's name and nothing else. Every scenario gets a
fresh world, a fresh clock and a fresh provider twin; a scenario with a
`generate` block gets one of each per case.

**Five outcomes, not two.** A suite that only says pass or fail cannot tell a
wrong agent from a missing capability or a broken binding, and those go to
different people:

| status | means | usually the fault of |
|---|---|---|
| `passed` | every check held | — |
| `failed` | a check did not hold | the agent, or the spec it was built from |
| `unrunnable` | it needs something this implementation lacks (an approval | a capability  |
|              | queue, a desk, a model-driven customer offline)         | that is missing |
| `crashed` | the run raised | usually the binding |
| `invalid` | the scenario file itself does not load | the scenario |

A run that **overran** its model script is reported beside its status: the
implementation reached the model more often than the scenario's author
expected, so a pass may be for a different reason than the one written down,
and a failure may be the script's rather than the agent's.
"""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agenttwin.attacks import GeneratedCase
from agenttwin.binding import Binding
from agenttwin.checks import Outcome
from agenttwin.loader import load
from agenttwin.perturbation import perturbed
from agenttwin.projection import Live
from agenttwin.provider import ProviderTwin, Upstream
from agenttwin.scenario import Clock
from agenttwin.scenario_file import InvalidScenario, ScenarioFile, load_scenario
from agenttwin.suite import (
    Unrunnable,
    generated_cases,
    plant,
    provider_faults,
    run_file,
    script_for,
    timeline_for,
)
from agenttwin.world import World

STATUSES = ("passed", "failed", "unrunnable", "crashed", "invalid")
RANK = {status: i for i, status in enumerate(STATUSES)}


@dataclass
class CaseResult:
    case: str
    status: str
    origin: str = ""
    """Where a generated case came from (`templates`, `pyrit …`, `agentdojo …`)."""
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    model_calls: int = 0
    overran: int = 0
    error: str = ""
    seconds: float = 0.0


@dataclass
class ScenarioResult:
    file: str
    scenario: str
    status: str
    discharges: list[str] = field(default_factory=list)
    forces: str = ""
    cases: list[CaseResult] = field(default_factory=list)
    error: str = ""

    @property
    def overran(self) -> int:
        return sum(c.overran for c in self.cases)

    @property
    def failed_checks(self) -> list[str]:
        return [
            f"{c.case + ': ' if c.case else ''}{o['check']} — {o['detail']}"
            for c in self.cases
            for o in c.outcomes
            if not o["passed"]
        ]


Voice = Callable[[str, str], Awaitable[str]]


async def run_one(
    path: Path,
    binding: Binding,
    *,
    upstream: Upstream | None = None,
    voice: Voice | None = None,
    timeout_s: float = 180.0,
) -> ScenarioResult:
    try:
        scenario = load_scenario(path)
    except InvalidScenario as exc:
        return ScenarioResult(file=path.name, scenario=path.stem, status="invalid", error=str(exc))

    result = ScenarioResult(
        file=path.name,
        scenario=scenario.scenario,
        status="passed",
        discharges=list(scenario.discharges),
        forces=scenario.forces,
    )
    if scenario.actor.kind == "model" and voice is None:
        result.status = "unrunnable"
        result.error = "needs a model-driven customer, which only a live run has"
        return result

    try:
        world = load(path.parent / scenario.world)
        generated = generated_cases(scenario, world) or [None]
    except Exception as exc:  # noqa: BLE001 — a source that cannot load is a result
        result.status, result.error = "crashed", f"{type(exc).__name__}: {exc}"
        return result
    for generated_case in generated:
        case = await _run_case(
            path, scenario, binding, world, generated_case, upstream, voice, timeout_s
        )
        result.cases.append(case)
    result.status = max((c.status for c in result.cases), key=RANK.__getitem__)
    return result


async def _run_case(
    path: Path,
    scenario: ScenarioFile,
    binding: Binding,
    world: World,
    generated: GeneratedCase | None,
    upstream: Upstream | None,
    voice: Voice | None,
    timeout_s: float,
) -> CaseResult:
    started = time.monotonic()
    name = generated.name if generated is not None else ""
    live = Live.start(world)
    if generated is not None:
        plant(live, scenario, generated.payload)
    timeline = timeline_for(scenario)
    clock = Clock(step_s=scenario.step_seconds)
    twin = ProviderTwin(
        script=() if upstream is not None else script_for(scenario, generated),
        faults=provider_faults(scenario),
        clock=clock,
        upstream=upstream,
    )
    case = CaseResult(
        case=name, status="passed", origin=generated.origin if generated is not None else ""
    )
    try:
        async with twin:
            wrap = perturbed(live, timeline, clock)
            async with binding(live, wrap=wrap, clock=clock, model=twin.endpoint) as subject:
                _, outcomes = await asyncio.wait_for(
                    run_file(
                        path,
                        subject=subject,
                        live=live,
                        timeline=timeline,
                        voice=voice,
                        clock=clock,
                        case=generated,
                    ),
                    timeout=timeout_s,
                )
        checked = list(outcomes)
        if twin.faults:
            checked.append(
                Outcome(
                    check="every declared provider fault fired",
                    passed=not twin.unfired,
                    detail="; ".join(f"model call {at}" for at in twin.unfired),
                )
            )
        case.outcomes = [
            {"check": o.check, "passed": o.passed, "detail": o.detail} for o in checked
        ]
        case.status = "passed" if all(o.passed for o in checked) else "failed"
    except Unrunnable as exc:
        case.status, case.error = "unrunnable", str(exc)
    except TimeoutError:
        case.status, case.error = "crashed", f"did not finish within {timeout_s:.0f}s"
    except Exception as exc:  # noqa: BLE001 — a crash is a result, not a stop
        frames = traceback.format_exception(exc)
        case.status, case.error = "crashed", "".join(frames[-3:]).strip()
    case.model_calls, case.overran = twin.calls, twin.overran
    case.seconds = round(time.monotonic() - started, 2)
    return case


async def run_suite(
    paths: Sequence[Path],
    binding: Binding,
    *,
    upstream: Upstream | None = None,
    voice: Voice | None = None,
    repeat: int = 1,
    on_result: Callable[[ScenarioResult], None] | None = None,
) -> list[ScenarioResult]:
    """Every scenario, `repeat` times each. Sequential on purpose: scenarios
    share nothing, but a live provider's rate limit is shared by all of them."""
    results = []
    for path in paths:
        for _ in range(repeat):
            result = await run_one(path, binding, upstream=upstream, voice=voice)
            results.append(result)
            if on_result is not None:
                on_result(result)
    return results


def scenario_paths(targets: Sequence[str]) -> list[Path]:
    """Files as given; directories as every `*.yaml` in them, sorted."""
    paths: list[Path] = []
    for target in targets:
        p = Path(target)
        paths.extend(sorted(p.glob("*.yaml")) if p.is_dir() else [p])
    return paths


def voice_for(upstream: Upstream) -> Voice:
    """A customer played by the upstream model — used to *speak*, never to grade."""
    import httpx2

    async def speak(brief: str, heard: str) -> str:
        opening = "Open the conversation — say what you want, in your own words."
        async with httpx2.AsyncClient(timeout=upstream.timeout_s) as client:
            answer = await client.post(
                f"{upstream.base_url.rstrip('/')}/chat/completions",
                json={
                    "model": upstream.model,
                    "max_tokens": 2048,
                    "messages": [
                        {"role": "system", "content": brief},
                        {"role": "user", "content": heard or opening},
                    ],
                },
                headers={"authorization": f"Bearer {upstream.api_key}"},
            )
        answer.raise_for_status()
        return answer.json()["choices"][0]["message"]["content"] or ""

    return speak


def summary(results: Sequence[ScenarioResult]) -> dict[str, Any]:
    counts = {status: 0 for status in STATUSES}
    for r in results:
        counts[r.status] += 1
    by_scenario: dict[str, list[ScenarioResult]] = {}
    for r in results:
        by_scenario.setdefault(r.file, []).append(r)
    rates = {
        file: round(sum(r.status == "passed" for r in runs) / len(runs), 2)
        for file, runs in by_scenario.items()
    }
    return {"counts": counts, "pass_rate": rates, "overran": sum(r.overran > 0 for r in results)}


def to_json(results: Sequence[ScenarioResult], **meta: Any) -> str:
    return json.dumps(
        {
            **meta,
            "summary": summary(results),
            "scenarios": [
                {**asdict(r), "overran": r.overran, "failed_checks": r.failed_checks}
                for r in results
            ],
        },
        indent=2,
    )


__all__ = [
    "STATUSES",
    "CaseResult",
    "ScenarioResult",
    "run_one",
    "run_suite",
    "scenario_paths",
    "summary",
    "to_json",
    "voice_for",
]
