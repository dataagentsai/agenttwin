"""`python -m agenttwin <command>`.

    run       drive scenario files against an implementation's binding
    scaffold  start AgentTwin for a new agent from its AOAS

Run from the implementation's own directory and environment, because the
binding imports the implementation:

    uv run python -m agenttwin run --binding evals.gate_binding:open_subject \\
        --out reports/behaviour.json ../reference-agent/scenarios

    # live: a real model answers, the customer too where a scenario asks for one
    uv run python -m agenttwin run --binding ... --live \\
        --upstream https://api.groq.com/openai/v1 --api-key-env GROQ_API_KEY \\
        --model openai/gpt-oss-120b --repeat 5 scenarios/
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def _run(args: argparse.Namespace) -> int:
    from agenttwin.binding import load_binding
    from agenttwin.provider import Upstream
    from agenttwin.runner import run_suite, scenario_paths, summary, to_json, voice_for

    binding = load_binding(args.binding)
    upstream = voice = None
    if args.live:
        key = os.environ.get(args.api_key_env, "")
        if not (args.upstream and args.model and key):
            print("--live needs --upstream, --model and a key in --api-key-env", file=sys.stderr)
            return 2
        upstream = Upstream(base_url=args.upstream, api_key=key, model=args.model)
        voice = voice_for(upstream)

    paths = scenario_paths(args.scenarios)
    if not paths:
        print("no scenario files found", file=sys.stderr)
        return 2

    def show(r) -> None:
        if args.quiet:
            return
        note = f"  (model overran its script ×{r.overran})" if r.overran else ""
        print(f"  {r.status:<10} {r.file}{note}")
        for line in (r.failed_checks or ([r.error] if r.error else []))[:4]:
            print(f"             {line.splitlines()[0][:150]}")
        for c in r.cases:
            if c.error and c.status != "unrunnable":
                print(f"             {c.case or 'run'}: {c.error.splitlines()[-1][:150]}")

    results = asyncio.run(
        run_suite(
            paths, binding, upstream=upstream, voice=voice, repeat=args.repeat, on_result=show
        )
    )
    counts = summary(results)["counts"]
    print("  " + " · ".join(f"{n} {status}" for status, n in counts.items() if n))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            to_json(
                results,
                binding=args.binding,
                mode="live" if args.live else "scripted",
                model=args.model if args.live else "scripted",
                repeat=args.repeat,
            )
        )
    return 0 if counts["passed"] == len(results) else 1


def _scaffold(args: argparse.Namespace) -> int:
    from agenttwin.scaffold import scaffold

    written = scaffold(Path(args.aoas), Path(args.out), force=args.force)
    for path in written:
        print(f"  wrote {path}")
    if not written:
        print("  nothing written: every file already exists (--force to overwrite)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agenttwin")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="drive scenario files against a binding")
    run.add_argument("scenarios", nargs="+", help="scenario files, or directories of them")
    run.add_argument("--binding", required=True, help="module:attribute of the binding")
    run.add_argument("--out", help="write the full result as JSON here")
    run.add_argument("--live", action="store_true", help="a real model answers")
    run.add_argument("--upstream", help="live: the provider's OpenAI-compatible base URL")
    run.add_argument("--model", help="live: the model name the provider serves")
    run.add_argument("--api-key-env", default="AGENTTWIN_API_KEY")
    run.add_argument("--repeat", type=int, default=1, help="runs per scenario (pass rates)")
    run.add_argument("-q", "--quiet", action="store_true")
    run.set_defaults(func=_run)

    scaffold = commands.add_parser("scaffold", help="start AgentTwin for a new agent")
    scaffold.add_argument("aoas", help="the agent's AOAS file")
    scaffold.add_argument("--out", required=True, help="the agent's repository")
    scaffold.add_argument("--force", action="store_true", help="overwrite existing files")
    scaffold.set_defaults(func=_scaffold)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
