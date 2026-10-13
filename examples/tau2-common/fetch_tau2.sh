#!/usr/bin/env bash
# Fetch τ²-bench at the pinned commit, outside this repository, for every
# τ² example (airline, retail, telecom). Prints the checkout's path.
#
# Only what the examples read is checked out (sparse): the package source, the
# three domains' data without their voice task files, the user simulator's
# guidelines, and the licence. Nothing is vendored in this repository but each
# domain's policy text and the licence (each example's vendor/tau2/).
set -euo pipefail
COMMIT=4ce7c0397c1eb65c9bbe59aeacfe1ca44a1cd699
TAU2_HOME="${TAU2_HOME:-$HOME/.cache/agenttwin/tau2-bench}"
PATTERNS=(
  /src
  /data/tau2/domains/airline
  /data/tau2/domains/retail
  /data/tau2/domains/telecom
  '!/data/tau2/domains/*/tasks_voice.json'
  '!/data/tau2/domains/*/audio_difficulty.json'
  '!/data/tau2/domains/telecom/tasks_full.json'
  '!/data/tau2/domains/*/task_issues/'
  '!/data/tau2/domains/telecom/workflows/'
  /data/tau2/user_simulator
  /LICENSE
  /pyproject.toml
  /README.md
)
ready() {
  [ -d "$TAU2_HOME/.git" ] \
    && [ "$(git -C "$TAU2_HOME" rev-parse HEAD)" = "$COMMIT" ] \
    && [ "$(git -C "$TAU2_HOME" sparse-checkout list)" = "$(printf '%s\n' "${PATTERNS[@]}")" ]
}
if ready; then echo "$TAU2_HOME"; exit 0; fi
if [ -d "$TAU2_HOME/.git" ] && [ "$(git -C "$TAU2_HOME" rev-parse HEAD)" = "$COMMIT" ]; then
  # The airline-only checkout of an earlier version: widen it in place.
  git -C "$TAU2_HOME" sparse-checkout set --no-cone "${PATTERNS[@]}" >&2
  echo "$TAU2_HOME"; exit 0
fi
mkdir -p "$(dirname "$TAU2_HOME")"
rm -rf "$TAU2_HOME"
git clone --quiet --filter=blob:none --no-checkout https://github.com/sierra-research/tau2-bench.git "$TAU2_HOME"
git -C "$TAU2_HOME" sparse-checkout set --no-cone "${PATTERNS[@]}"
git -C "$TAU2_HOME" checkout --quiet "$COMMIT"
echo "$TAU2_HOME"
