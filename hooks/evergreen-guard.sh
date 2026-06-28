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

# Engage only on git commit/add intent. Loose grep; a false match just runs a no-op staged check.
printf '%s' "$STDIN" | grep -Eq 'git[[:space:]]+(commit|add)([[:space:]]|")' || exit 0

ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT" 2>/dev/null || exit 0

staged="$(git diff --cached --name-only 2>/dev/null)" || exit 0
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
    AUDIT-*.md|SUMMARY.md|SYNTHESIS.md|*-REVIEW.md|*-REVIEW-LOG.md) echo "AI-slop report"; return 0 ;;
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
