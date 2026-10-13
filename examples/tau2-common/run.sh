#!/usr/bin/env bash
# Run a module of one τ² example with AgentTwin, this shared package and the
# pinned τ²-bench checkout. Called by each example's own run.sh:
#   tau2-common/run.sh <example dir> -m <module> [args]
set -euo pipefail
COMMON="$(cd "$(dirname "$0")" && pwd)"
HERE="$(cd "$1" && pwd)"; shift
TAU2_HOME="$("$COMMON/fetch_tau2.sh")"
export TAU2_DATA_DIR="$TAU2_HOME/data"
export PYTHONPATH="$COMMON${PYTHONPATH:+:$PYTHONPATH}"
cd "$HERE"
# audioop-lts: τ² imports audioop, which Python 3.13 no longer ships.
exec uv run --project "$COMMON/../.." --extra dev --with audioop-lts --with-editable "$TAU2_HOME" python "$@"
