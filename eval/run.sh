#!/usr/bin/env bash
# One measured eval run: a headless agent winnows the seeded fixture; score.py grades it.
# Usage: bash eval/run.sh          (EVAL_MODEL=<model> overrides the CLI's default model)
# The prompt = the SKILL body (frontmatter stripped) + prompt.md, so the eval always
# measures the ruleset as it currently ships.
set -euo pipefail
cd "$(dirname "$0")"
command -v claude >/dev/null 2>&1 || { echo "needs the claude CLI on PATH" >&2; exit 1; }
mkdir -p out
OUT="out/run-$(date +%Y%m%d-%H%M%S).txt"

PROMPT="$(
  awk 'NR==1 && /^---[[:space:]]*$/ {f=1; next} f && /^---[[:space:]]*$/ {f=0; next} !f' \
    ../skills/evergreen/SKILL.md
  printf '\n'
  cat prompt.md
)"

( cd fixture && claude -p "$PROMPT" --allowedTools "Read,Grep,Glob" \
    ${EVAL_MODEL:+--model "$EVAL_MODEL"} ) | tee "$OUT"
echo
echo "--- score ($OUT) ---"
python3 score.py "$OUT"
