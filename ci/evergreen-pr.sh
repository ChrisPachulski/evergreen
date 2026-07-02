#!/usr/bin/env bash
# Evergreen PR driver — runs the winnow (deep affirmative) pass on the docs a PR's code touched,
# and posts the findings as a single upserted PR comment. Comment-only: findings NEVER fail the
# build (evergreen's truth axis flags, the human decides). Mirrors eval/run.sh: the prompt is the
# frontmatter-stripped SKILL body + a PR-scoped winnow instruction, run headless with read-only
# tools. Every external call is guarded so a hiccup can't hard-fail CI. Always exits 0.
set -u

# --- locate SKILL + ci/ (works when another repo consumes us via `uses:`) ------------------------
ACTION_PATH="${EVERGREEN_ACTION_PATH:-$(cd "$(dirname "$0")/.." && pwd)}"
SKILL="$ACTION_PATH/skills/evergreen/SKILL.md"
COMMENT_PY="$ACTION_PATH/ci/pr_comment.py"

# The repo under review is the runner's checkout (the consuming repo), not the action's copy.
REPO_ROOT="${GITHUB_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$REPO_ROOT" 2>/dev/null || { echo "evergreen: cannot enter repo root, skipping." >&2; exit 0; }

MODE="${EVERGREEN_MODE:-winnow}"
POST_COMMENT="${EVERGREEN_POST_COMMENT:-true}"

emit_summary() {
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then cat >> "$GITHUB_STEP_SUMMARY"; else cat; fi
}

# --- resolve the diff base -----------------------------------------------------------------------
BASE="${EVERGREEN_BASE_REF:-}"
if [ -z "$BASE" ]; then
  if [ -n "${GITHUB_BASE_REF:-}" ]; then
    BASE="origin/$GITHUB_BASE_REF"
  else
    # note: no PR base (e.g. a push build) → diff against the previous commit; upgrade to a
    # merge-base if branch-vs-branch precision ever matters.
    BASE="HEAD^"
  fi
fi
git rev-parse --verify "$BASE" >/dev/null 2>&1 || {
  printf '### 🌲 evergreen\n\nCould not resolve diff base `%s` — nothing checked.\n' "$BASE" | emit_summary
  echo "evergreen: diff base '$BASE' not found, skipping." >&2
  exit 0
}

# --- gate: only run when non-doc code changed AND the repo tracks docs ---------------------------
changed="$(git diff --name-only "$BASE"...HEAD 2>/dev/null || true)"
code_changed=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  fl="$(printf '%s' "$f" | tr '[:upper:]' '[:lower:]')"
  case "$fl" in *.md|*.markdown|*.rst) continue ;; esac
  code_changed=1; break
done <<EOF
$changed
EOF

has_docs=0
git ls-files 2>/dev/null | grep -qiE '\.(md|markdown|rst)$' && has_docs=1

if [ "$code_changed" -eq 0 ] || [ "$has_docs" -eq 0 ]; then
  printf '### 🌲 evergreen\n\nNo code-with-docs changes in this PR — nothing to check.\n' | emit_summary
  echo "evergreen: no non-doc code change or no tracked docs; nothing to check." >&2
  exit 0
fi

command -v claude >/dev/null 2>&1 || {
  printf '### 🌲 evergreen\n\nClaude CLI not on PATH — skipped.\n' | emit_summary
  echo "evergreen: claude CLI missing, skipping." >&2
  exit 0
}
[ -r "$SKILL" ] || {
  printf '### 🌲 evergreen\n\nSKILL.md not found at %s — skipped.\n' "$SKILL" | emit_summary
  echo "evergreen: SKILL.md not readable, skipping." >&2
  exit 0
}

# --- build the prompt = SKILL body (frontmatter stripped) + PR winnow instruction ----------------
SKILL_BODY="$(awk 'NR==1 && /^---[[:space:]]*$/ {f=1; next} f && /^---[[:space:]]*$/ {f=0; next} !f' "$SKILL")"

read -r -d '' TASK <<EOF || true
# Task — PR winnow ($MODE)

The ruleset above is in force. This is a pull request. Its code diff is \`$BASE...HEAD\`. Run the
deep affirmative winnow pass, but scope it to ONLY the documentation claims that the diff could
have made false: grep the docs for the paths and symbols the diff touched, walk each affected
claim, and prove every finding against the current code (cite a code file:line). Judge only this
repository's docs against this repository's code. Exempt what leads or freezes (ADRs, specs,
CHANGELOG history, dated snapshots) — never flag those. Do not modify any file.

End your reply with a fenced block tagged \`jsonl\` containing one JSON object per finding and
nothing else. Emit no object when a surface still matches — silence is certification.

Each finding:
{"severity":"high|med|low","category":"in_docs_not_code|name_mismatch|in_code_not_docs","file":"<doc path>","line":<line>,"claim":"<the exact doc phrase that is wrong>","why":"<one line, citing code file:line>","fix_or_flag":"fix|flag"}

Only emit a finding you can prove against the code. The \`claim\` field must quote the doc's own words.
EOF

PROMPT="$SKILL_BODY

$TASK"

# --- run the winnow (guarded; never let a non-zero exit fail CI) ---------------------------------
RAW="$(claude -p "$PROMPT" --allowedTools "Read,Grep,Glob" 2>/dev/null || true)"
[ -n "$RAW" ] || {
  printf '### 🌲 evergreen\n\nThe winnow produced no output (model error or timeout) — nothing posted.\n' | emit_summary
  echo "evergreen: empty model output, skipping comment." >&2
  exit 0
}

# --- render Markdown -----------------------------------------------------------------------------
MD="$(printf '%s' "$RAW" | python3 "$COMMENT_PY" 2>/dev/null || true)"
[ -n "$MD" ] || {
  echo "evergreen: comment renderer produced nothing, skipping." >&2
  exit 0
}

printf '%s\n' "$MD" | emit_summary

# --- upsert the PR comment (single comment, keyed by the hidden marker) --------------------------
MARKER="<!-- evergreen-report -->"
if [ "$POST_COMMENT" = "true" ] && [ -n "${GITHUB_BASE_REF:-}" ] && command -v gh >/dev/null 2>&1; then
  PR="${GITHUB_REF_NAME:-}"                       # e.g. "42/merge" on pull_request events
  PR="${PR%%/*}"
  REPO="${GITHUB_REPOSITORY:-}"                    # guarded: unset would abort under set -u
  if [ -n "$PR" ] && [ -n "$REPO" ]; then
    tmp="$(mktemp 2>/dev/null || echo /tmp/evergreen-comment.md)"
    printf '%s\n' "$MD" > "$tmp"
    # Find an existing evergreen comment to edit; else create one. All calls guarded.
    existing="$(gh api "repos/$REPO/issues/$PR/comments" --jq \
      ".[] | select(.body | startswith(\"$MARKER\")) | .id" 2>/dev/null | head -n1 || true)"
    if [ -n "$existing" ]; then
      gh api -X PATCH "repos/$REPO/issues/comments/$existing" \
        -F body=@"$tmp" >/dev/null 2>&1 \
        && echo "evergreen: updated PR comment $existing." >&2 \
        || echo "evergreen: comment update failed (non-fatal)." >&2
    else
      gh pr comment "$PR" --body-file "$tmp" >/dev/null 2>&1 \
        && echo "evergreen: posted PR comment." >&2 \
        || echo "evergreen: comment post failed (non-fatal)." >&2
    fi
    rm -f "$tmp" 2>/dev/null || true
  fi
fi

exit 0
