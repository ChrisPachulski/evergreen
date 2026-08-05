#!/usr/bin/env bash
# One measured eval run: a headless agent seeds the fixture; score.py grades it.
# Usage: bash eval/seed/run.sh  (EVAL_MODEL=<model> overrides the CLI default)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
command -v claude >/dev/null 2>&1 || { echo "needs the claude CLI on PATH" >&2; exit 1; }
mkdir -p eval/seed/out
OUT="eval/seed/out/run-$(date +%Y%m%d-%H%M%S).txt"
TILL="$(python3 bin/evergreen till --repo . eval/fixture --json)"

PROMPT="$(
  awk 'NR==1 && /^---[[:space:]]*$/ {f=1; next} f && /^---[[:space:]]*$/ {f=0; next} !f' \
    skills/evergreen/SKILL.md
  printf '\n'
  cat commands/seed.md
  printf '\n\n## Pass-B provider inventory (quote verbatim)\n\n%s\n\n' "$TILL"
  cat eval/seed/prompt.md
)"

# Pass E certifies on an external provider, which needs to shell out. The default grant is
# read-only, so the judged run takes seed's documented fallback and the exam measures triage and
# claim discipline alone. SEED_EXAM_JUDGE=external widens the grant to exactly the judge entry
# point, so a run can exercise the real certification path — and costs one codex call.
TOOLS="Read,Grep,Glob"
if [ "${SEED_EXAM_JUDGE:-fallback}" = "external" ]; then
  TOOLS="$TOOLS,Bash(python3 -c \"from evergreen.judge import*\":*)"
fi

claude -p "$PROMPT" --allowedTools "$TOOLS" \
  ${EVAL_MODEL:+--model "$EVAL_MODEL"} | tee "$OUT"
echo
echo "--- score ($OUT) ---"
python3 eval/seed/score.py "$OUT"
