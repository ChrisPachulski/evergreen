#!/usr/bin/env bash
# Evergreen hygiene guard — PreToolUse(Bash) backstop for the cultivate axis.
# Before a `git commit`/`git add` runs, inspect the STAGED set and block if it carries files
# that have no business in version control: secrets, build artifacts, or AI-slop internal docs.
# The project root's index is inspected, plus every repository a literal `cd` or `git -C` in the
# command leads to; a repository or index the classifier cannot follow is refused.
# Before a `git tag evergreen--v*` creation runs, require committed winnow + cultivate
# certification records verified clean at HEAD (hooks/evergreen-release-gate.py), fail-closed.
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
# Fail-safe fallback (python3 unavailable). Pure shell cannot tokenize nested/escaped shell
# quoting exactly, so this path: extracts the JSON "command" value (missing/empty -> none, like
# the helper's malformed-payload branch), joins backslash-newline continuations, turns real
# newlines/tabs into spaces, removes QUOTED SPANS (content included) so quoted words never create
# intent, then requires an index-writing subcommand or commit in git-subcommand position (option
# tokens, each optionally followed by one value token, may intervene). It does not evaluate variables,
# substitutions, aliases, or other computed commands. Residual false positives — all fail-closed,
# on this degraded path only:
#   - an UNQUOTED word in subcommand-like position (e.g. `git log --grep add`) still registers
#     intent, so the staged index is inspected for a command that never writes it;
#   - eval / sh -c bodies are quoted and therefore opaque after span removal; when such a wrapper
#     appears alongside raw git+add/commit words the call is blocked as compound, even if the
#     body is one safe command or the words sit inside its quoted message;
#   - payloads carrying several "command" keys classify only the last one.
# A commit is also unsafe here when a positional pathspec follows its options; that check reads
# each quoted span as one word, so `-m "x" .env` still finds `.env` after the message.
FALLBACK_CMD="$(printf '%s' "$STDIN" | tr '\n' ' ' \
  | sed -nE 's/.*"command"[[:space:]]*:[[:space:]]*"(([^"\\]|\\.)*)".*/\1/p')"
JSON_CONTINUATION='\\\n'
JSON_NEWLINE='\n'
JSON_TAB='\t'
FALLBACK_CMD="${FALLBACK_CMD//"$JSON_CONTINUATION"/}"
FALLBACK_WORDS="${FALLBACK_CMD//"$JSON_NEWLINE"/ ; }"
FALLBACK_CMD="${FALLBACK_CMD//"$JSON_NEWLINE"/ }"
FALLBACK_CMD="${FALLBACK_CMD//"$JSON_TAB"/ }"
# Words for the pathspec check: a newline or control operator ends a command, and each quoted
# span becomes the one placeholder word `Q` it stands for.
FALLBACK_WORDS="$(printf '%s' "${FALLBACK_WORDS//"$JSON_TAB"/ }" \
  | sed -e 's/\\"[^"]*\\"/Q/g' -e "s/'[^']*'/Q/g" -e 's/[;&|()]/ ; /g' | tr -d '\\')"
# Shell double quotes arrive JSON-escaped as \" — remove those spans first, then single-quoted
# spans, then join the remaining backslash-split word fragments.
FALLBACK_TEXT="$(printf '%s' "$FALLBACK_CMD" \
  | sed -e 's/\\"[^"]*\\"//g' -e "s/'[^']*'//g" | tr -d '\\')"
# Option tokens after `git`, each optionally followed by one value token (e.g. -C <path>).
GIT_OPTION_GAP='([[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*'
# Every subcommand that can write the index, mirroring STAGING_SUBCOMMANDS in the Python
# classifier: paired with a commit in one call they stage content this hook never inspects.
GIT_STAGING='(add|mv|rm|apply|stash|restore|checkout|reset|update-index|read-tree|cherry-pick|revert|merge|rebase|am)'

fallback_has_git_intent() {
  local wanted="$1"
  printf '%s' "$FALLBACK_TEXT" | grep -Eq \
    "(^|[^[:alnum:]_-])git${GIT_OPTION_GAP}[[:space:]]+${wanted}([^[:alnum:]_-]|\$)"
}

# True when a `git commit` names a positional pathspec (`git commit -m x .env`), which commits that
# path's working-tree content whatever the inspected index holds.
fallback_commit_has_pathspec() {
  local word git_seen=false in_commit=false value=false
  local -a words
  read -ra words <<< "$FALLBACK_WORDS"
  for word in "${words[@]}"; do
    if [ "$word" = ";" ]; then
      git_seen=false; in_commit=false; value=false
      continue
    fi
    if ! $in_commit; then
      case "$word" in git|*/git) git_seen=true ;; commit) $git_seen && in_commit=true ;; esac
      continue
    fi
    if $value; then
      value=false
      continue
    fi
    case "$word" in
      -m|-F|-C|-c|--message|--file|--author|--date|--reuse-message|--reedit-message|--fixup|--squash|--cleanup|--trailer|--gpg-sign) value=true ;;
      --*) ;;
      -*[mFCc]) value=true ;;
      -*) ;;
      # A spaced heredoc or here-string operator owns its delimiter word, as `>` owns its target.
      '>'|'>>'|'<'|'>|'|'<>'|'<<'|'<<-'|'<<<'|[0-9]'>'|[0-9]'>>'|[0-9]'<'|[0-9]'<>'|[0-9]'<<'|[0-9]'<<-'|[0-9]'<<<') value=true ;;
      *'>'*|*'<'*) ;;
      *) return 0 ;;
    esac
  done
  return 1
}

fallback_intent() {
  if [ -z "$FALLBACK_CMD" ]; then
    echo none
    return
  fi
  # Coarse release-tag check on the RAW command text: a wrapped or quoted evergreen--v* tag
  # creation must not slip through the degraded path, so quoted spans are NOT stripped first.
  # Residual false positive (this degraded path only, fail-closed): prose carrying the words
  # git, tag, and an evergreen--v ref in one command. The release gate itself needs python3,
  # so everything this classifies is refused with the standing certification message.
  if printf '%s' "$FALLBACK_CMD" | grep -q 'evergreen--v' \
     && printf '%s' "$FALLBACK_CMD" | grep -Eq '(^|[^[:alnum:]_-])git([^[:alnum:]_-]|$)' \
     && printf '%s' "$FALLBACK_CMD" | grep -Eq '(^|[^[:alnum:]_-])tag([^[:alnum:]_-]|$)'; then
    echo release-tag
    return
  fi
  local add=false staging=false commit=false
  fallback_has_git_intent add && add=true
  fallback_has_git_intent "$GIT_STAGING" && staging=true
  fallback_has_git_intent commit && commit=true
  # Wrapper rescan: quoted eval/sh -c bodies vanished with the quoted spans above, so a coarse
  # raw-text match must conservatively block what the stripped text can no longer see.
  if ! $staging && ! $commit \
     && printf '%s' "$FALLBACK_CMD" | grep -Eq \
       '(^|[^[:alnum:]_-])(eval|(ba|z)?sh[[:space:]]+-c)([^[:alnum:]_-]|$)' \
     && printf '%s' "$FALLBACK_CMD" | grep -Eq \
       "(^|[^[:alnum:]_-])git.*(${GIT_STAGING}|commit)([^[:alnum:]_-]|\$)"; then
    echo compound
    return
  fi
  if $staging && $commit; then
    echo compound
  # An option must OPEN a token. Without the leading boundary the short-flag branch also matched
  # the tail of any hyphenated word — `identifier-shaped` in a commit-message body read as a
  # cluster containing `a`/`p` — so ordinary prose blocked a safe commit on this degraded path.
  elif $commit && { printf '%s' "$FALLBACK_TEXT" | grep -Eq \
      'commit([^[:alnum:]_-]|$)(.*[[:space:]])?(--(all|include|only|interactive|patch|pathspec-from-file|pathspec-file-nul)([=[:space:]]|$)|-[[:alpha:]]*[aiop][[:alpha:]]*([[:space:]]|$))' \
      || fallback_commit_has_pathspec; }; then
    echo unsafe
  elif $add || $commit; then
    echo git
  else
    echo none
  fi
}

classified=""
if command -v python3 >/dev/null 2>&1; then
  classified="$(printf '%s' "$STDIN" | python3 "$SCRIPT_DIR/evergreen-guard-command.py" 2>/dev/null)" || classified=""
fi
# Line one is the intent. Any further lines name directories, besides the root, that the command
# may run git in (a literal `cd` or `git -C`); each is inspected exactly like the root.
intent="${classified%%$'\n'*}"
targets=""
[ "$intent" = "$classified" ] || targets="${classified#*$'\n'}"
case "$intent" in
  compound|unsafe|unresolved|git|none|release-tag|release-elsewhere|release-compound) ;;
  *) intent="$(fallback_intent)"; targets="" ;;
esac

# Any call containing both intents is deliberately rejected. Shell grammar is too broad to prove
# ordering or exclusivity safely at this trust boundary.
if [ "$intent" = "compound" ]; then
  echo "evergreen guard: run the index-writing command (add, mv, rm, apply, stash, restore, checkout, reset, and the other history commands) and git commit in separate Bash tool calls so the finalized staged index can be inspected, or set EVERGREEN_GUARD=off to bypass deliberately." >&2
  exit 2
fi

# Inspecting the project's index while the commit lands in another repository or index would
# certify the wrong set of files, so a location the classifier cannot follow is refused, and so is
# one it names that does not exist yet (created, copied, or moved earlier in the same command).
refuse_unresolved() {
  echo "evergreen guard: cannot tell which repository or index this commit or release tag uses (a cd or git -C to a computed path or one that does not exist yet, more than one relative cd, more directories than the guard inspects, GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE/GIT_COMMON_DIR, --git-dir/--work-tree, or a substitution nested too deep or too ambiguous to read); run it with literal paths and the default index, or set EVERGREEN_GUARD=off to bypass deliberately." >&2
  exit 2
}
[ "$intent" = "unresolved" ] && refuse_unresolved

if [ "$intent" = "unsafe" ]; then
  echo "evergreen guard: commit modes that can read unstaged working-tree content are blocked; use a separately staged plain commit, or set EVERGREEN_GUARD=off to bypass deliberately." >&2
  exit 2
fi

# An evergreen--v* release tag is a certification claim. Creation is allowed only when the
# fail-closed gate verifies committed winnow + cultivate records at HEAD; tag deletion,
# listing, and verification were classified none and passed through above.
if [ "$intent" = "release-compound" ]; then
  echo "evergreen guard: create an evergreen--v* release tag in its own Bash call, separate from git add/commit, or set EVERGREEN_GUARD=off to bypass deliberately." >&2
  exit 2
fi

if [ "$intent" = "release-elsewhere" ]; then
  echo "evergreen guard: an evergreen--v* release tag certifies the checked-out HEAD only; check out the certified commit and tag it without an explicit committish, or set EVERGREEN_GUARD=off to bypass deliberately." >&2
  exit 2
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
# The root, then each target resolved from it as the shell would; a target that does not resolve
# prints an empty line.
repo_dirs() {
  printf '%s\n' "$ROOT"
  [ -n "$targets" ] || return 0
  while IFS= read -r target; do
    (cd "$ROOT" 2>/dev/null && cd "$target" 2>/dev/null && pwd) || echo
  done <<EOF
$targets
EOF
}

if [ "$intent" = "release-tag" ]; then
  certified=false
  if command -v python3 >/dev/null 2>&1; then
    certified=true
    while IFS= read -r dir; do
      if [ -z "$dir" ] || ! python3 "$SCRIPT_DIR/evergreen-release-gate.py" --repo "$dir"; then
        certified=false
        break
      fi
    done < <(repo_dirs)
  fi
  $certified && exit 0
  echo "evergreen guard: refusing the evergreen--v* release tag — it requires winnow and cultivate certification records committed at HEAD and verified clean (the release gate names the failing check above; without python3 the records cannot be verified at all). Run the winnow and cultivate passes, commit their records, and retag, or set EVERGREEN_GUARD=off to bypass deliberately." >&2
  exit 2
fi

[ "$intent" = "git" ] || exit 0

# Escape hatch (b): .evergreen-keep declares legit paths (one glob/pattern per line, # comments).
keep_match() {
  local keep="$1" f="$2" pat
  [ -r "$keep" ] || return 1
  while IFS= read -r pat; do
    [ -z "$pat" ] && continue
    case "$pat" in \#*) continue ;; esac
    # shellcheck disable=SC2254
    case "$f" in $pat) return 0 ;; esac
  done < "$keep"
  return 1
}

# High-signal block patterns. Echoes the reason; returns 0 on a hit.
is_slop() {
  local f="$1" b="${1##*/}"
  case "$b" in
    # `*.env` catches bare `backend.env`/`staging.env`; `.env`/`.env.*` stay for the dotfile forms.
    # SSH keys are listed as exact basenames (ssh-keygen(1) FILES), never `id_*`, so the `.pub`
    # public half — legitimately committable — is not swept up.
    .env|.env.*|*.env|*.pem|*.key|id_rsa|id_dsa|id_ecdsa|id_ecdsa_sk|id_ed25519|id_ed25519_sk|*.p12|*.keystore) echo "secret/credential"; return 0 ;;
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

# Each repository is inspected once, however many directories lead to it. Names are read raw and
# NUL-delimited: by default git C-quotes a non-ASCII name (`"cl\303\251.pem"`), and the quotes
# defeated both the `*.pem` and the `dist/*` patterns.
# --diff-filter=d EXCLUDES staged deletions: a commit that *removes* slop/secrets is the guard
# doing its job, not a violation. Without this it blocks cleanup commits — the exact deletions it
# exists to enforce. Adds/copies/modifies/renames still get inspected.
hits=""
seen=""
while IFS= read -r dir; do
  [ -n "$dir" ] || refuse_unresolved
  top="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)" || continue
  [ -n "$top" ] || continue
  case "$seen" in *"|$top|"*) continue ;; esac
  where=""
  keep="$top/.evergreen-keep"
  if [ "$dir" = "$ROOT" ]; then keep="$ROOT/.evergreen-keep"; elif [ -n "$seen" ]; then where=" (in $top)"; fi
  seen="$seen|$top|"
  while IFS= read -r -d '' f; do
    keep_match "$keep" "$f" && continue
    if why="$(is_slop "$f")"; then
      hits="${hits}  • ${f} — ${why}${where}"$'\n'
    fi
  done < <(git -C "$top" -c core.quotePath=false diff --cached --name-only -z --diff-filter=d 2>/dev/null)
done < <(repo_dirs)

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
