#!/usr/bin/env bash
# Run a module of this example with AgentTwin and the pinned τ²-bench checkout.
#   ./run.sh -m tau2_telecom.offline           # the offline comparison
#   ./run.sh -m pytest tests                  # this example's tests
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$HERE/../tau2-common/run.sh" "$HERE" "$@"
