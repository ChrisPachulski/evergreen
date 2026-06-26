#!/usr/bin/env bash
# Evergreen Stop-hook — a non-blocking nudge. Pattern credit: Jan-ARN/drift.
# Evergreen is a SKILL (LLM behavior), not a scanner: this hook does no drift analysis
# of its own. It only notices "you changed code in a repo that has docs" and reminds the
# agent to run the evergreen freshness reflex. Three cheap git guards, then a nudge;
# exits 0 ALWAYS (never blocks).
set -u

# Guard 1: inside a git work tree.
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
# Guard 2: code (non-doc) actually changed in the working tree.
git diff --name-only 2>/dev/null | grep -qvEi '\.(md|markdown|rst)$' || exit 0
# Guard 3: the repo actually has tracked docs that could now be wrong.
git ls-files 2>/dev/null | grep -qiE '\.(md|markdown|rst)$' || exit 0

printf '{"systemMessage":"evergreen: you changed code in a repo with docs — run the freshness reflex on the changed surfaces (paths, contracts, snippets, then prose). Prove each finding against the code or drop it. (warn-only)"}\n'
exit 0
