#!/usr/bin/env bash
# Evergreen hygiene guard — PreToolUse(Bash) backstop for the cultivate axis.
# Before a `git commit`/`git add` runs, inspect the STAGED set and block if it carries files
# that have no business in version control: secrets, build artifacts, or AI-slop internal docs.
# High-signal patterns only (cultivate ladder rung 2) — never the heuristic rungs, so it can't
# nag. The truth/craft axes never block; the hygiene axis may, because a leaked secret or slop
# dump is irreversible once pushed. Escape hatches provided. Never blocks on its own errors.
#
# Exit 2 = block (reason on stderr, fed back to the agent). Exit 0 = allow.
set -u

# Escape hatch (c): disable the guard entirely for a run.
[ "${EVERGREEN_GUARD:-on}" = "off" ] && exit 0

STDIN="$(cat 2>/dev/null || true)"
SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)" || exit 0
# Fail-safe fallback applies the same simple quote/backslash joining as the JSON helper. It does not
# attempt to evaluate variables, substitutions, aliases, or other computed commands.
JSON_CONTINUATION='\\\n'
FALLBACK_TEXT="${STDIN//"$JSON_CONTINUATION"/}"
FALLBACK_TEXT="$(printf '%s' "$FALLBACK_TEXT" | tr -d '\\' | tr -d "'" | tr -d '"')"

fallback_has_git_intent() {
  local wanted="$1"
  printf '%s' "$FALLBACK_TEXT" | grep -Eq \
    "(^|[^[:alnum:]_-])git.*${wanted}([^[:alnum:]_-]|$)"
}

fallback_intent() {
  local add=false commit=false
  fallback_has_git_intent add && add=true
  fallback_has_git_intent commit && commit=true
  if $add && $commit; then
    echo compound
  elif $add || $commit; then
    echo git
  else
    echo none
  fi
}

intent=""
if command -v python3 >/dev/null 2>&1; then
  intent="$(printf '%s' "$STDIN" | python3 "$SCRIPT_DIR/evergreen-guard-command.py" 2>/dev/null)" || intent=""
fi
case "$intent" in
  compound|git|none) ;;
  *) intent="$(fallback_intent)" ;;
esac

# Any call containing both intents is deliberately rejected. Shell grammar is too broad to prove
# ordering or exclusivity safely at this trust boundary.
if [ "$intent" = "compound" ]; then
  echo "evergreen guard: run git add and git commit in separate Bash tool calls so the finalized staged index can be inspected, or set EVERGREEN_GUARD=off to bypass deliberately." >&2
  exit 2
fi

[ "$intent" = "git" ] || exit 0

ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT" 2>/dev/null || exit 0

# --diff-filter=d EXCLUDES staged deletions: a commit that *removes* slop/secrets is the guard
# doing its job, not a violation. Without this it blocks cleanup commits — the exact deletions it
# exists to enforce. Adds/copies/modifies/renames still get inspected.
staged="$(git diff --cached --name-only --diff-filter=d 2>/dev/null)" || exit 0
[ -z "$staged" ] && exit 0

# Escape hatch (b): .evergreen-keep declares legit paths (one glob/pattern per line, # comments).
KEEP="$ROOT/.evergreen-keep"
keep_match() {
  [ -r "$KEEP" ] || return 1
  local f="$1" pat
  while IFS= read -r pat; do
    [ -z "$pat" ] && continue
    case "$pat" in \#*) continue ;; esac
    # shellcheck disable=SC2254
    case "$f" in $pat) return 0 ;; esac
  done < "$KEEP"
  return 1
}

# High-signal block patterns. Echoes the reason; returns 0 on a hit.
is_slop() {
  local f="$1" b="${1##*/}"
  case "$b" in
    .env|.env.*|*.pem|*.key|id_rsa|id_ed25519|*.p12|*.keystore) echo "secret/credential"; return 0 ;;
    .DS_Store|Thumbs.db) echo "OS cruft"; return 0 ;;
    # Bare SUMMARY.md is NOT here: mdBook mandates src/SUMMARY.md and GitBook uses a root
    # SUMMARY.md as its TOC — a legit, sometimes mandatory file fails the high-signal bar.
    AUDIT-*.md|*_SUMMARY.md|*-SUMMARY.md|SYNTHESIS.md|*-REVIEW.md|*-REVIEW-LOG.md) echo "AI-slop report"; return 0 ;;
  esac
  case "$f" in
    .planning/*|.research/*) echo "internal planning dump"; return 0 ;;
    node_modules/*|*/node_modules/*|dist/*|*/dist/*|build/*|*/build/*|target/*|*/target/*|*/__pycache__/*) echo "build artifact / cache"; return 0 ;;
  esac
  return 1
}

hits=""
while IFS= read -r f; do
  [ -z "$f" ] && continue
  keep_match "$f" && continue
  if why="$(is_slop "$f")"; then
    hits="${hits}  • ${f} — ${why}"$'\n'
  fi
done <<EOF
$staged
EOF

[ -z "$hits" ] && exit 0

{
  echo "evergreen guard: this commit stages files that look like they don't belong in version control:"
  printf '%s' "$hits"
  echo "Resolve with one of:"
  echo "  (a) unstage — 'git rm --cached <file>' + add to .gitignore (keeps the file on disk);"
  echo "  (b) if it's genuinely legit, add the path to .evergreen-keep and retry;"
  echo "  (c) to bypass the guard for this run, set EVERGREEN_GUARD=off."
  echo "The hygiene axis may block — a leaked secret or slop dump is irreversible once pushed — but you keep the final call."
} >&2
exit 2
