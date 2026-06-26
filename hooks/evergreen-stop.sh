#!/usr/bin/env bash
# Evergreen Stop-hook — a non-blocking, post-turn audit REQUEST (not drift detection).
# Pattern credit: Jan-ARN/drift. Evergreen is a SKILL: this hook does NO doc analysis of its
# own. It only notices "you changed code in a repo that has docs" and asks the agent to run the
# freshness pass. Reads the persisted mode (silent when off). Three git guards inspect git STATE
# and file EXTENSION only — never doc content. Exits 0 ALWAYS (never blocks).
set -u

EG_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
[ "${EG_ROOT#/}" = "$EG_ROOT" ] && EG_ROOT="$PWD"   # ensure absolute
MODE_FILE="$EG_ROOT/.evergreen-mode"
if [ -r "$MODE_FILE" ]; then
  m="$(tr -d '[:space:]' < "$MODE_FILE" 2>/dev/null || true)"
  [ "$m" = "off" ] && exit 0
fi

# All git checks run against EG_ROOT, not the hook's cwd (which may differ from the project).
g() { git -C "$EG_ROOT" "$@"; }

# Guard 1: inside a git work tree.
g rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
# Guard 2: code (non-doc) actually changed — staged, unstaged, OR untracked.
changed="$( { g diff --name-only HEAD; g ls-files --others --exclude-standard; } 2>/dev/null )"
code_changed=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  fl="$(printf '%s' "$f" | tr '[:upper:]' '[:lower:]')"
  case "$fl" in *.md|*.markdown|*.rst) continue ;; esac
  code_changed=1; break
done <<EOF
$changed
EOF
[ "$code_changed" -eq 1 ] || exit 0
# Guard 3: the repo actually has tracked docs that could now be wrong.
g ls-files 2>/dev/null | grep -qiE '\.(md|markdown|rst)$' || exit 0

printf '{"systemMessage":"evergreen: you changed code in a repo with docs — run the freshness pass on the changed surfaces (paths, contracts, snippets, then prose). Prove each finding against the code or drop it."}\n'
exit 0
