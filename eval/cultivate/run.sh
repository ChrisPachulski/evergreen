#!/usr/bin/env bash
# One measured cultivate run in a real disposable repository.
# Usage: bash eval/cultivate/run.sh  (EVAL_MODEL=<model> overrides the CLI default)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
command -v claude >/dev/null 2>&1 || { echo "needs the claude CLI on PATH" >&2; exit 1; }
mkdir -p "$ROOT/eval/cultivate/out"
OUT="$ROOT/eval/cultivate/out/run-$(date +%Y%m%d-%H%M%S).txt"
REPO="$(bash "$ROOT/eval/cultivate/setup.sh")"

cleanup() {
  find "$REPO" -depth -delete
}
trap cleanup EXIT

PROMPT="$(
  awk 'NR==1 && /^---[[:space:]]*$/ {f=1; next} f && /^---[[:space:]]*$/ {f=0; next} !f' \
    "$ROOT/skills/evergreen/SKILL.md"
  printf '\n'
  cat "$ROOT/commands/cultivate.md"
  printf '\n'
  cat "$ROOT/eval/cultivate/prompt.md"
)"

(
  cd "$REPO"
  claude -p "$PROMPT" --allowedTools "Bash,Read,Grep,Glob" \
    ${EVAL_MODEL:+--model "$EVAL_MODEL"}
) | tee "$OUT"
echo
echo "--- score ($OUT) ---"
python3 "$ROOT/eval/cultivate/score.py" "$OUT"
