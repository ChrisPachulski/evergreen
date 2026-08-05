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

# The judged run happens OUTSIDE this repository. Running it in place lets the CLI walk up from
# fixture/ to evergreen's own CLAUDE.md, .claude/, and hooks; under an autonomous loop the session
# then answers as that loop instead of winnowing — observed 2026-08-05: a status report, no jsonl
# block, and score.py read every counter as zero. That is indistinguishable from a total ruleset
# regression, which is the worst way for an exam to fail.
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/winnow-exam.XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
git -C .. archive HEAD eval/fixture | tar -x -C "$SANDBOX" --strip-components=2
[ -f "$SANDBOX/README.md" ] || { echo "fixture did not materialise" >&2; exit 1; }

( cd "$SANDBOX" && claude -p "$PROMPT" --allowedTools "Read,Grep,Glob" \
    ${EVAL_MODEL:+--model "$EVAL_MODEL"} ) | tee "$OUT"
echo
echo "--- score ($OUT) ---"
python3 score.py "$OUT"
