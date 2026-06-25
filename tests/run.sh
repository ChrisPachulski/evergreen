#!/usr/bin/env bash
# tests/run.sh — standalone test suite for bin/evergreen-scan.
# Pure bash; no frameworks. Each test builds a throwaway git repo in a mktemp dir
# (mirroring the engine's selftest()) and asserts the engine's REAL output.
# Run: bash tests/run.sh   (from anywhere). Exits nonzero if any test fails.
set -u

SCAN="$(cd "$(dirname "$0")/.." && pwd)/bin/evergreen-scan"
[ -x "$SCAN" ] || { echo "FATAL: engine not found/executable at $SCAN"; exit 1; }

PASS=0; FAIL=0

ok(){   PASS=$((PASS+1)); echo "PASS: $1"; }
bad(){  FAIL=$((FAIL+1)); echo "FAIL: $1"; [ -n "${2:-}" ] && printf '  ---\n%s\n  ---\n' "$2"; }

# newrepo: make a fresh git repo in a mktemp dir, echo its path. Caller fills it.
newrepo(){
  local t; t="$(mktemp -d)"
  ( cd "$t" && git init -q && git config user.email t@t && git config user.name t ) || return 1
  echo "$t"
}

# --- 1. JSON output is valid and count matches finding lines ------------------
test_json(){
  local t out cnt; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs
    printf 'x\n' > src/keep.txt
    printf '# Doc\nSee `src/GONE.swift` and run `--ghost-flag`.\n' > docs/g.md
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" --json 2>/dev/null)"
  rm -rf "$t"

  # Valid JSON? prefer python3/jq, else structural grep.
  local valid=0
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$out" | python3 -c 'import sys,json;json.load(sys.stdin)' >/dev/null 2>&1 && valid=1
  elif command -v jq >/dev/null 2>&1; then
    printf '%s' "$out" | jq . >/dev/null 2>&1 && valid=1
  else
    # structural: starts {"findings":[ ... and ends ],"count":N}
    printf '%s' "$out" | grep -q '^{"findings":\[' && printf '%s' "$out" | grep -qE '\],"count":[0-9]+}$' && valid=1
  fi
  [ "$valid" = 1 ] && ok "json: output is valid JSON" || bad "json: output is valid JSON" "$out"

  # count field == number of finding objects.
  cnt="$(printf '%s' "$out" | grep -oE '"count":[0-9]+' | grep -oE '[0-9]+')"
  local objs; objs="$(printf '%s' "$out" | grep -o '"category":' | grep -c '"category":')"
  if [ "$cnt" = "$objs" ] && [ "${cnt:-0}" -ge 1 ]; then
    ok "json: count ($cnt) equals number of findings ($objs)"
  else
    bad "json: count equals number of findings (count=$cnt objs=$objs)" "$out"
  fi
}

# --- 2. --ci exit codes ------------------------------------------------------
test_ci(){
  # (a) clean repo -> exit 0
  local t rc; t="$(newrepo)"
  ( cd "$t" || exit 1; mkdir -p src; printf 'x\n' > src/a.txt; git add -A && git commit -qm init ) >/dev/null 2>&1
  ( cd "$t" && bash "$SCAN" --ci --fail-level high >/dev/null 2>&1 ); rc=$?
  [ "$rc" -eq 0 ] && ok "ci: clean repo exits 0" || bad "ci: clean repo exits 0 (got $rc)"
  rm -rf "$t"

  # Build a repo whose ONLY finding is medium (a GHOST_VAR env token; no flags, no
  # missing paths). env contract = medium severity.
  t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs
    printf 'real code\n' > src/code.txt
    printf '# Doc\nSet `GHOST_VAR` somewhere.\n' > docs/g.md
    git add -A && git commit -qm init
  ) >/dev/null 2>&1

  # sanity: the only finding really is medium (no high lines)
  local human; human="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  if printf '%s' "$human" | grep -q '\[medium\]' && ! printf '%s' "$human" | grep -q '\[high\]'; then
    ok "ci: medium-only fixture has exactly a medium finding"
  else
    bad "ci: medium-only fixture has exactly a medium finding" "$human"
  fi

  # (b) medium finding, --fail-level high -> exit 0 (below threshold)
  ( cd "$t" && bash "$SCAN" --ci --fail-level high >/dev/null 2>&1 ); rc=$?
  [ "$rc" -eq 0 ] && ok "ci: medium finding below high threshold exits 0" \
                   || bad "ci: medium finding below high threshold exits 0 (got $rc)"

  # (c) medium finding, --fail-level medium -> exit 2
  ( cd "$t" && bash "$SCAN" --ci --fail-level medium >/dev/null 2>&1 ); rc=$?
  [ "$rc" -eq 2 ] && ok "ci: medium finding at medium threshold exits 2" \
                   || bad "ci: medium finding at medium threshold exits 2 (got $rc)"
  rm -rf "$t"
}

# --- 3. EXEMPT subtrees not flagged ------------------------------------------
test_exempt(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs/specs adr
    printf 'x\n' > src/keep.txt
    printf '# Spec\nWill add `src/FUTURE.swift`.\n' > docs/specs/foo.md
    printf '# ADR\nPlan `src/LATER.swift`.\n'        > adr/0001.md
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  rm -rf "$t"
  if ! printf '%s' "$out" | grep -qE 'FUTURE\.swift|LATER\.swift'; then
    ok "exempt: missing paths cited only in specs/adr are not flagged"
  else
    bad "exempt: missing paths cited only in specs/adr are not flagged" "$out"
  fi
}

# --- 4. EXT suppression: extensionless real-root tokens not flagged -----------
test_ext(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs
    printf 'x\n' > src/keep.txt
    # real top-level root "src" but no file extension -> not file-like -> ignored
    printf '# Doc\nHit `src/health` and `src/v1/health`.\n' > docs/g.md
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  rm -rf "$t"
  if ! printf '%s' "$out" | grep -qE 'src/health|src/v1/health'; then
    ok "ext: extensionless real-root paths are not flagged"
  else
    bad "ext: extensionless real-root paths are not flagged" "$out"
  fi
}

# --- 5. Rung-3 contract false-positive discipline ----------------------------
test_contract(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs
    # code references the real flag and real var; all-caps-no-underscore is prose.
    printf 'run with --real-flag\nREAL_VAR=1\n' > src/code.txt
    {
      printf '# Doc\n'
      printf 'Use `--real-flag` (in code) and `--ghost-flag` (absent).\n'
      printf 'Set `REAL_VAR` (in code) and `GHOST_VAR` (absent).\n'
      printf 'Returns `JSON` over `HTTP`.\n'   # no underscore -> never env drift
    } > docs/g.md
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  rm -rf "$t"

  printf '%s' "$out" | grep -q -- 'documents flag `--ghost-flag` not found in code' \
    && ok "contract: --ghost-flag (absent) is flagged" \
    || bad "contract: --ghost-flag (absent) is flagged" "$out"

  printf '%s' "$out" | grep -q 'documents env/config `GHOST_VAR` not found in code' \
    && ok "contract: GHOST_VAR (absent) is flagged" \
    || bad "contract: GHOST_VAR (absent) is flagged" "$out"

  printf '%s' "$out" | grep -q -- '--real-flag' \
    && bad "contract: --real-flag (in code) is NOT flagged" "$out" \
    || ok "contract: --real-flag (in code) is not flagged"

  printf '%s' "$out" | grep -q 'REAL_VAR' \
    && bad "contract: REAL_VAR (in code) is NOT flagged" "$out" \
    || ok "contract: REAL_VAR (in code) is not flagged"

  printf '%s' "$out" | grep -qE 'config `JSON`|config `HTTP`' \
    && bad "contract: all-caps-no-underscore (JSON/HTTP) is NOT flagged" "$out" \
    || ok "contract: all-caps-no-underscore (JSON/HTTP) is not flagged"
}

# --- 6. Rung-4 example execution: opt-in + operator-consent gated -------------
test_examples(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs
    printf 'x\n' > src/keep.txt
    {
      printf '# Doc\n'
      printf '```bash evergreen\nexit 3\n```\n'   # tagged -> run only with --run-examples
      printf '```bash\nexit 3\n```\n'             # untagged -> never executed
    } > docs/g.md
    git add -A && git commit -qm init
  ) >/dev/null 2>&1

  # default (no --run-examples): tagged block must NOT run -> no example finding.
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'runnable example' \
    && bad "examples: default run does NOT execute (operator-consent gate)" "$out" \
    || ok "examples: default run does not execute (operator-consent gate)"

  # with --run-examples: tagged fails, untagged still never runs (exactly one finding).
  out="$(cd "$t" && bash "$SCAN" --run-examples 2>/dev/null)"
  rm -rf "$t"
  printf '%s' "$out" | grep -q 'runnable example #1 exits nonzero (3)' \
    && ok "examples: --run-examples runs tagged block, flags exit 3" \
    || bad "examples: --run-examples runs tagged block, flags exit 3" "$out"
  local n; n="$(printf '%s\n' "$out" | grep -c 'runnable example')"
  [ "$n" = 1 ] && ok "examples: untagged block is not executed even with --run-examples" \
              || bad "examples: untagged block is not executed even with --run-examples (findings=$n)" "$out"
}

# --- 8. Hook never auto-executes doc code (no unprompted RCE) -----------------
test_hook_no_rce(){
  local t marker; t="$(newrepo)"; marker="$HOME/.evergreen_test_rce_$$"
  rm -f "$marker"
  ( cd "$t" || exit 1
    mkdir -p src docs; printf 'code\n' > src/a.py
    printf '# Doc\n```bash evergreen\ntouch "%s"\n```\n' "$marker" > docs/g.md
    git add -A && git commit -qm init
    printf 'changed\n' > src/a.py            # code change so the hook's guards pass
  ) >/dev/null 2>&1
  ( cd "$t" && CLAUDE_PLUGIN_ROOT="$(cd "$(dirname "$SCAN")/.." && pwd)" \
      bash "$(cd "$(dirname "$SCAN")/.." && pwd)/hooks/evergreen-stop.sh" >/dev/null 2>&1 )
  if [ -e "$marker" ]; then bad "hook: does NOT execute tracked-doc code (RCE)"; rm -f "$marker"; else ok "hook: does not execute tracked-doc code (no RCE)"; fi
  rm -rf "$t"
}

# --- 9. Contract precision: prose tokens (outside backticks) not flagged ------
test_contract_prose(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs; printf 'x\n' > src/a.py
    printf '# Doc\nA **BOLD_TEXT**, MAX_SIZE, TODO_LIST in prose; CSS --my-color, --with-foo too.\n' > docs/g.md
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  rm -rf "$t"
  if ! printf '%s' "$out" | grep -qE 'BOLD_TEXT|MAX_SIZE|TODO_LIST|--my-color|--with-foo'; then
    ok "contract: prose tokens outside backticks are not flagged"
  else
    bad "contract: prose tokens outside backticks are not flagged" "$out"
  fi
}

# --- 10. Contract boundary: substring is not a match (no false negative) ------
test_contract_boundary(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs
    printf -- '--verbose-mode\nAPI_KEY_SECRET=1\n' > src/code.txt   # only the LONGER tokens exist
    printf '# Doc\nUse `--verbose` and `API_KEY`.\n' > docs/g.md     # shorter docs tokens are drift
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  rm -rf "$t"
  printf '%s' "$out" | grep -q -- 'documents flag `--verbose` not found in code' \
    && ok "contract: --verbose not satisfied by --verbose-mode (boundary)" \
    || bad "contract: --verbose not satisfied by --verbose-mode (boundary)" "$out"
  printf '%s' "$out" | grep -q 'documents env/config `API_KEY` not found in code' \
    && ok "contract: API_KEY not satisfied by API_KEY_SECRET (boundary)" \
    || bad "contract: API_KEY not satisfied by API_KEY_SECRET (boundary)" "$out"
}

# --- 11. Spaced doc filename is scanned (no false green) ----------------------
test_spaced_filename(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs; printf 'x\n' > src/a.py
    printf '# Doc\nSee `src/GONE.swift`.\n' > "docs/my guide.md"
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  rm -rf "$t"
  printf '%s' "$out" | grep -q 'GONE.swift' \
    && ok "spaced-filename: doc with a space in its path is still scanned" \
    || bad "spaced-filename: doc with a space in its path is still scanned (false green!)" "$out"
}

# --- 12. Arg validation ------------------------------------------------------
test_args(){
  local t rc; t="$(newrepo)"
  ( cd "$t" || exit 1; mkdir -p src; printf 'x\n' > src/a.txt; git add -A && git commit -qm init ) >/dev/null 2>&1
  ( cd "$t" && bash "$SCAN" --fail-level bogus >/dev/null 2>&1 ); rc=$?
  [ "$rc" -eq 1 ] && ok "args: bad --fail-level rejected (exit 1)" || bad "args: bad --fail-level rejected (got $rc)"
  ( cd "$t" && bash "$SCAN" --base >/dev/null 2>&1 ); rc=$?
  [ "$rc" -eq 1 ] && ok "args: dangling --base shows usage, no crash (exit 1)" || bad "args: dangling --base (got $rc)"
  rm -rf "$t"
}

# --- 7. git guard: outside a repo -> exit 1, message on stderr, no clean line --
test_gitguard(){
  local t out err rc; t="$(mktemp -d)"   # deliberately NOT a git repo
  err="$(cd "$t" && bash "$SCAN" 2>&1 >/dev/null)"; rc=$?
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  rm -rf "$t"
  [ "$rc" -eq 1 ] && ok "gitguard: non-repo exits 1" || bad "gitguard: non-repo exits 1 (got $rc)"
  printf '%s' "$err" | grep -q 'not a git repository' \
    && ok "gitguard: prints 'not a git repository' to stderr" \
    || bad "gitguard: prints 'not a git repository' to stderr" "$err"
  printf '%s' "$out" | grep -q 'no drift' \
    && bad "gitguard: does NOT print clean 'no drift' line" "$out" \
    || ok "gitguard: does not print clean 'no drift' line"
}

test_json
test_ci
test_exempt
test_ext
test_contract
test_examples
test_gitguard
test_hook_no_rce
test_contract_prose
test_contract_boundary
test_spaced_filename
test_args

echo "----------------------------------------"
echo "summary: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
