#!/usr/bin/env bash
# Fetch τ²-bench at the pinned commit, outside this repository.
#
# Only what the airline example reads is checked out (sparse): the package
# source, the airline domain's data, and the licence. Nothing is vendored here
# but the policy text and the licence (vendor/tau2/).
set -euo pipefail
COMMIT=4ce7c0397c1eb65c9bbe59aeacfe1ca44a1cd699
TAU2_HOME="${TAU2_HOME:-$HOME/.cache/agenttwin/tau2-bench}"
if [ -d "$TAU2_HOME/.git" ] && [ "$(git -C "$TAU2_HOME" rev-parse HEAD)" = "$COMMIT" ]; then
  echo "$TAU2_HOME"; exit 0
fi
mkdir -p "$(dirname "$TAU2_HOME")"
rm -rf "$TAU2_HOME"
git clone --quiet --filter=blob:none --no-checkout https://github.com/sierra-research/tau2-bench.git "$TAU2_HOME"
git -C "$TAU2_HOME" sparse-checkout set --no-cone /src /data/tau2/domains/airline /data/tau2/user_simulator /LICENSE /pyproject.toml /README.md
git -C "$TAU2_HOME" checkout --quiet "$COMMIT"
echo "$TAU2_HOME"
