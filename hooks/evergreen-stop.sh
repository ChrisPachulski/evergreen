#!/usr/bin/env bash
# Evergreen Stop-hook — a non-blocking nudge. Pattern credit: Jan-ARN/drift.
# Three guards, then a quiet deterministic scan; exits 0 ALWAYS (never blocks).
set -u
ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
SCAN="$ROOT/bin/evergreen-scan"

# Guard 1: inside a git work tree.
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
# Guard 2: code (non-doc) actually changed in the working tree.
git diff --name-only 2>/dev/null | grep -qvEi '\.(md|markdown|rst)$' || exit 0
# Guard 3: the repo actually has tracked docs to be wrong.
git ls-files 2>/dev/null | grep -qiE '\.(md|markdown|rst)$' || exit 0

out="$(bash "$SCAN" --base HEAD 2>/dev/null)"
echo "$out" | grep -qi 'in_docs_not_code' || exit 0

n="$(printf '%s\n' "$out" | grep -ci 'in_docs_not_code')"
printf '{"systemMessage":"evergreen: %s doc-drift finding(s) from your code changes — run `evergreen-scan` or ask me to review. (warn-only)"}\n' "$n"
exit 0
