#!/usr/bin/env bash
# Regenerate the 180 attack cases and run them against both reference agents,
# offline: a scripted model, no provider, no key. Writes results/.
#
#   examples/attacks/run.sh
#
# Needs uv, and the family checked out beside this repository (or set
# AGENTTWIN_FAMILY to the directory that holds them):
#   agenttwin  reference-agent  claims-fnol-azure  clean-ai-engineering
# About five minutes on a laptop; PyRIT (about 550 MB) is installed on first use.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
FAMILY="${AGENTTWIN_FAMILY:-$(dirname "$ROOT")}"
WORK="${WORK:-$(mktemp -d)}"
PYRIT="pyrit==1.1.0"

for repo in reference-agent claims-fnol-azure clean-ai-engineering; do
  [ -d "$FAMILY/$repo" ] || { echo "missing $FAMILY/$repo (clone github.com/dataagentsai/$repo beside agenttwin)" >&2; exit 2; }
done

# Offline by construction: no key reaches the run, so nothing can call a model.
unset GROQ_API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY AGENTTWIN_API_KEY AZURE_OPENAI_API_KEY || true

echo "work: $WORK"
python3 "$HERE/record.py" prepare --family "$FAMILY" --work "$WORK"
(cd "$ROOT" && uv run --quiet --extra dev python "$HERE/record.py" cases --work "$WORK")

run_agent() {  # $1 agent label, $2 repository it runs in
  echo "running $1 in $2 ..."
  (cd "$FAMILY/$2" && PYTHONPATH="$ROOT" uv run --quiet --frozen --with "$PYRIT" \
     python -m agenttwin run -q --binding evals.gate_binding:open_subject \
     --out "$WORK/$1.json" "$WORK/$1") > "$WORK/$1.log" 2>&1 || true
  tail -1 "$WORK/$1.log"
}
run_agent support reference-agent
run_agent motor-claims claims-fnol-azure

PYRIT_VERSION="${PYRIT#pyrit==}" python3 "$HERE/record.py" summarize --family "$FAMILY" \
  --work "$WORK" --out "$HERE/results" --original "$HERE/results/original-0.11.0.json"
