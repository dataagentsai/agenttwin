#!/usr/bin/env bash
# Run a module of this example with AgentTwin and the pinned τ²-bench checkout.
#   ./run.sh -m tau2_airline.offline          # the offline comparison
#   ./run.sh -m pytest tests                  # this example's tests
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TAU2_HOME="$("$HERE/fetch_tau2.sh")"
export TAU2_DATA_DIR="$TAU2_HOME/data"
cd "$HERE"
# audioop-lts: τ² imports audioop, which Python 3.13 no longer ships.
exec uv run --project "$HERE/../.." --extra dev --with audioop-lts --with-editable "$TAU2_HOME" python "$@"
