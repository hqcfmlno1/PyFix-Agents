#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_PATH="${1:-.}"
SYMPTOM_FILE="${ARENA_SYMPTOM_FILE:-SYMPTOM.txt}"
REPORT_PATH="${ARENA_REPORT:-agent_report.json}"

ARGS=(
  --non-interactive
  --repo "$REPO_PATH"
  --report "$REPORT_PATH"
)

if [ -f "$SYMPTOM_FILE" ]; then
  ARGS+=(--symptom-file "$SYMPTOM_FILE")
elif [ -n "${ARENA_SYMPTOM:-}" ]; then
  ARGS+=(--symptom-text "$ARENA_SYMPTOM")
else
  echo "No symptom source found: missing $SYMPTOM_FILE and ARENA_SYMPTOM is empty" >&2
  exit 2
fi

exec env PYFIX_NON_INTERACTIVE=1 "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/main.py" "${ARGS[@]}"
