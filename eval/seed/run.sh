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

# The judge is mandatory on every run: pass B's inventory and any pass-E doc go to the second
# provider, so the grant carries exactly the judge entry point and the task names it. No codex on
# the host is an abstention the scorer fails, never a silent pass. The entry point is absolute
# because the judged run executes in a bare sandbox that holds only the fixture.
TOOLS="Read,Grep,Glob,Bash(python3 $ROOT/bin/evergreen judge:*)"
PROMPT="$PROMPT

## Judge entry point

Run the judge as \`python3 $ROOT/bin/evergreen judge\`; \`--repo .\` is this sandbox, and the pass-B
inventory is saved at \`till.json\` in it for \`--inventory\`."

# The judged run happens OUTSIDE this repository, in a bare copy of the fixture. Running it in
# place lets the CLI walk up to evergreen's own CLAUDE.md, .claude/, and hooks, and a session
# under an autonomous loop then answers as that loop instead of doing the exam — observed
# 2026-08-05: the run returned a loop status report, no jsonl, and the scorer read every counter
# as zero, which is indistinguishable from a total ruleset regression. The fixture path structure
# is preserved so the pre-computed till inventory's `eval/fixture/...` paths still resolve.
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/seed-exam.XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
git -C "$ROOT" archive HEAD eval/fixture | tar -x -C "$SANDBOX"
[ -d "$SANDBOX/eval/fixture" ] || { echo "fixture did not materialise" >&2; exit 1; }
# The judge's --inventory wants a file and the grant has no write tool, so the runner saves the
# pass-B inventory beside the fixture; the task names the path.
printf '%s\n' "$TILL" > "$SANDBOX/till.json"

( cd "$SANDBOX" && claude -p "$PROMPT" --allowedTools "$TOOLS" \
    ${EVAL_MODEL:+--model "$EVAL_MODEL"} ) | tee "$OUT"
echo
echo "--- score ($OUT) ---"
python3 eval/seed/score.py "$OUT"
