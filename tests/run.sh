#!/usr/bin/env bash
# tests/run.sh — standalone test suite for bin/evergreen-scan.
# Pure bash; no frameworks. Each test builds a throwaway git repo in a mktemp dir
# (mirroring the engine's selftest()) and asserts the engine's REAL output.
# Run: bash tests/run.sh   (from anywhere). Exits nonzero if any test fails.
set -u

SCAN="$(cd "$(dirname "$0")/.." && pwd)/bin/evergreen-scan"
GOLDEN="$(cd "$(dirname "$0")" && pwd)/golden/expected.json"
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

# --- 19. Edge hardening: embed-fence collision, CRLF manifest, indented embed -
test_edge_hardening(){
  local t out; t="$(newrepo)"
  # (a) embed source range containing a ``` fence: must refuse, never corrupt, converge
  ( cd "$t" || exit 1
    mkdir -p src docs
    printf 'fn a() {}\n```\nfn b() {}\n' > src/x.txt
    printf '# Doc\n<!-- evergreen:embed src/x.txt:1-3 -->\n```rust\nfn a() {}\n```\nprose tail\n' > docs/g.md
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'contains a code fence' && ! printf '%s' "$out" | grep -q 'has drifted' \
    && ok "embed: source containing a fence is refused (not mis-reported as drift)" || bad "embed: fence-collision refused" "$out"
  cp "$t/docs/g.md" "$t/.before"
  ( cd "$t" && bash "$SCAN" --fix >/dev/null 2>&1 )
  cmp -s "$t/.before" "$t/docs/g.md" && ok "embed: --fix leaves a fence-containing embed UNCHANGED (no corruption)" || bad "embed: --fix corrupted doc"
  rm -rf "$t"

  # (b) CRLF in manifest must not cause a false needs_reverify
  t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs; printf 'v1\n' > src/a.py; printf '# d\n' > docs/a.md
    printf 'docs/a.md\tsrc/a.py\t%s\r\n' "$(git hash-object src/a.py)" > .evergreen-manifest
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'no drift' && ok "manifest: CRLF line is tolerated (no false needs_reverify)" || bad "manifest: CRLF false positive" "$out"
  rm -rf "$t"

  # (c) embed marker/fence indented in a list is still detected
  t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs; printf 'A\nB\nC\n' > src/s.txt
    printf '# Doc\n1. item\n   <!-- evergreen:embed src/s.txt:1-2 -->\n   ```\n   A\n   WRONG\n   ```\n' > docs/g.md
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'drifted from source' && ok "embed: indented (list) embed is detected" || bad "embed: indented embed detected" "$out"
  rm -rf "$t"
}

# --- 18. --fix applies derivable fixes only; never edits prose ---------------
test_fix_engine(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs
    printf 'fn a() {}\nfn b() {}\n' > src/lib.rs; printf 'v1\n' > src/api.py
    { printf '# Doc\nProse cites `src/DEAD.swift` (gone).\n<!-- evergreen:embed src/lib.rs:1-2 -->\n```rust\nfn a() {}\nfn b() {}\n```\n'; } > docs/g.md
    printf 'docs/g.md\tsrc/api.py\t%s\n' "$(git hash-object src/api.py)" > .evergreen-manifest
    git add -A && git commit -qm init
    printf 'fn a() {}\nfn RENAMED() {}\n' > src/lib.rs       # embed drift
    printf 'v2\n' > src/api.py                                # manifest drift
  ) >/dev/null 2>&1
  ( cd "$t" && bash "$SCAN" --fix >/dev/null 2>&1 )
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  # derivable fixed (embed + manifest gone), prose dead-ref preserved AND still flagged
  if ! printf '%s' "$out" | grep -qE 'drifted from source|needs_reverify' \
     && printf '%s' "$out" | grep -q 'src/DEAD.swift' \
     && grep -q 'src/DEAD.swift' "$t/docs/g.md" && grep -q 'fn RENAMED' "$t/docs/g.md"; then
    ok "fix: refreshes embed + re-pins manifest, leaves prose untouched"
  else
    bad "fix: derivable-only, prose preserved" "$out"
  fi
  rm -rf "$t"
}

# --- 17. Coverage: count, fail-under gate, baseline ratchet ------------------
test_coverage(){
  local t out rc; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src
    printf 'def documented(x):\n    """d"""\n    return x\n\ndef undocumented(y):\n    return y\n' > src/a.py
    printf '/// docs\npub fn good() {}\npub fn bad() {}\n' > src/c.rs
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  # 2 documented (documented, good) of 4 total (documented, undocumented, good, bad) = 50%
  out="$(cd "$t" && bash "$SCAN" --coverage 2>/dev/null)"
  printf '%s' "$out" | grep -q '50% (2/4' && ok "coverage: counts public symbols + doc-comments (50%)" || bad "coverage: 50% (2/4)" "$out"

  out="$(cd "$t" && bash "$SCAN" --coverage --json 2>/dev/null)"
  printf '%s' "$out" | grep -q '"coverage_pct":50' && ok "coverage: json shape" || bad "coverage: json shape" "$out"

  ( cd "$t" && bash "$SCAN" --coverage --ci --fail-under 80 >/dev/null 2>&1 ); rc=$?
  [ "$rc" -eq 2 ] && ok "coverage: --ci below fail-under exits 2" || bad "coverage: fail-under gate (got $rc)"
  ( cd "$t" && bash "$SCAN" --coverage --ci --fail-under 40 >/dev/null 2>&1 ); rc=$?
  [ "$rc" -eq 0 ] && ok "coverage: --ci above fail-under exits 0" || bad "coverage: above fail-under (got $rc)"

  # set baseline, then drop coverage -> ratchet fails even above fail-under
  ( cd "$t" && bash "$SCAN" --coverage --fix >/dev/null 2>&1 )           # baseline 50
  ( cd "$t" && printf 'def another_undoc(z):\n    return z\n' >> src/a.py && git add -A && git commit -qm more ) >/dev/null 2>&1
  ( cd "$t" && bash "$SCAN" --coverage --ci --fail-under 1 >/dev/null 2>&1 ); rc=$?
  [ "$rc" -eq 2 ] && ok "coverage: drop below baseline trips ratchet (exit 2)" || bad "coverage: ratchet (got $rc)"
  rm -rf "$t"
}

# --- 17d. AST-backed Python coverage: exact, ignores comments/strings ---------
test_coverage_ast(){
  command -v python3 >/dev/null 2>&1 || { ok "coverage-ast: skipped (no python3)"; return; }
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src
    printf 'def top():\n    """d"""\nclass S:\n    """c"""\n    def m_doc(self):\n        """d"""\n    def m_undoc(self):\n        pass\n    def _hidden(self):\n        pass\n    def __init__(self):\n        pass\n# def comment_fake():\nx = "def string_fake():"\n' > src/a.py
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" --coverage 2>/dev/null)"
  # documented: top, S, m_doc = 3 ; total: + m_undoc = 4 ; _hidden/__init__/comment/string excluded
  printf '%s' "$out" | grep -q '75% (3/4' && printf '%s' "$out" | grep -q 'py: ast' \
    && ok "coverage-ast: exact, excludes comment/string/dunder/_private (75%)" || bad "coverage-ast: exact 75% (3/4)" "$out"
  # a syntax-error file must not crash the run
  ( cd "$t" && printf 'def broken(:\n' > src/bad.py && git add -A && git commit -qm bad ) >/dev/null 2>&1
  ( cd "$t" && bash "$SCAN" --coverage >/dev/null 2>&1 ) && ok "coverage-ast: parse-error file falls back without crashing" || bad "coverage-ast: parse-error crashed"
  rm -rf "$t"
}

# --- 17e. Parser-backed JS/TS coverage via deno doc (skips without deno) ------
test_coverage_deno(){
  { command -v deno >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; } || { ok "coverage-deno: skipped (needs deno+python3)"; return; }
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src
    printf '/** d */\nexport function foo(){}\nexport function bar(){}\nexport class Svc {\n  /** m */\n  run(){}\n  helper(){}\n}\n' > src/a.ts
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" --coverage 2>/dev/null)"
  # exported: foo(doc) bar Svc + methods run(doc) helper => documented 2 / total 5 = 40%
  printf '%s' "$out" | grep -q '40% (2/5' && printf '%s' "$out" | grep -q 'js/ts: deno' \
    && ok "coverage-deno: exact exported symbols + methods via deno doc (40%)" || bad "coverage-deno: 40% (2/5) via deno" "$out"
  rm -rf "$t"
}

# --- 17c. Coverage badge: inject, idempotent, json-safe fallback -------------
test_coverage_badge(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src
    printf 'def a():\n    """d"""\n    return 1\ndef b():\n    return 2\n' > src/x.py   # 50%
    printf '# P\n<!-- evergreen:badge:start -->\nOLD\n<!-- evergreen:badge:end -->\n' > README.md
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  ( cd "$t" && bash "$SCAN" --coverage --badge >/dev/null 2>&1 )
  if grep -q 'docs_coverage-50%25-yellow' "$t/README.md" && ! grep -q '^OLD$' "$t/README.md"; then
    ok "badge: injected between markers (replaces old)"
  else bad "badge: injected between markers" "$(cat "$t/README.md")"; fi
  # idempotent
  local b; b="$(cat "$t/README.md")"; ( cd "$t" && bash "$SCAN" --coverage --badge >/dev/null 2>&1 )
  [ "$b" = "$(cat "$t/README.md")" ] && ok "badge: idempotent" || bad "badge: idempotent"
  # no markers + --json -> stdout stays valid json
  ( cd "$t" && rm -f README.md )
  out="$(cd "$t" && bash "$SCAN" --coverage --badge --json 2>/dev/null)"
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$out" | python3 -c 'import sys,json;json.load(sys.stdin)' >/dev/null 2>&1 \
      && ok "badge: no-marker fallback keeps --json stdout valid" || bad "badge: json-safe fallback" "$out"
  else
    printf '%s' "$out" | grep -q '^{"coverage_pct"' && ok "badge: no-marker fallback keeps --json stdout valid" || bad "badge: json-safe fallback" "$out"
  fi

  # start marker but NO end marker -> file must be left byte-for-byte unchanged (no data loss)
  ( cd "$t" && printf 'BEFORE\n<!-- evergreen:badge:start -->\nKEEP_1\nKEEP_2\n' > README.md )
  cp "$t/README.md" "$t/.bk"
  ( cd "$t" && bash "$SCAN" --coverage --badge >/dev/null 2>&1 )
  cmp -s "$t/.bk" "$t/README.md" && ok "badge: missing end marker leaves README untouched (no data loss)" || bad "badge: data loss on missing end marker" "$(cat "$t/README.md")"
  rm -rf "$t"
}

# --- 17b. Coverage counts methods/nested (python), excludes _private ----------
test_coverage_methods(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src
    printf 'def top():\n    """d"""\n    return 1\n\nclass S:\n    """c"""\n    def m_doc(self):\n        """d"""\n        return 2\n    def m_undoc(self):\n        return 3\n    async def a_undoc(self):\n        return 4\n    def _hidden(self):\n        return 5\n' > src/a.py
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" --coverage 2>/dev/null)"
  # documented: top, S, m_doc = 3; total adds m_undoc + a_undoc = 5; _hidden excluded -> 60%
  printf '%s' "$out" | grep -q '60% (3/5' && ok "coverage: counts methods + async def, excludes _private (60%)" || bad "coverage: method-aware (60%)" "$out"
  rm -rf "$t"
}

# --- 16. SHA-pinned manifest: pin / source-change / --fix re-pin / missing ----
test_manifest(){
  local t out sha; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs; printf 'v1\n' > src/api.py; printf '# API\n' > docs/api.md
    sha="$(git hash-object src/api.py)"
    { printf '# manifest\n'; printf 'docs/api.md\tsrc/api.py\t%s\n' "$sha"; } > .evergreen-manifest
    git add -A && git commit -qm init
  ) >/dev/null 2>&1

  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'no drift' && ok "manifest: pinned-match is clean" || bad "manifest: pinned-match is clean" "$out"

  ( cd "$t" && printf 'v2\n' > src/api.py )
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'needs_reverify' && printf '%s' "$out" | grep -q 'changed since verified' \
    && ok "manifest: source change -> needs_reverify (medium)" || bad "manifest: source change -> needs_reverify" "$out"

  ( cd "$t" && bash "$SCAN" --fix >/dev/null 2>&1 )
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'no drift' && ok "manifest: --fix re-pins, re-scan clean" || bad "manifest: --fix re-pins" "$out"

  ( cd "$t" && rm -f src/api.py )
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'manifest source `src/api.py` no longer exists' \
    && ok "manifest: deleted source -> high" || bad "manifest: deleted source -> high" "$out"
  rm -rf "$t"
}

# --- 16b. Region-pinned (method-level) manifest ------------------------------
test_manifest_region(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs
    printf 'l1\nfn target() {\n  body\n}\nl5\nl6\n' > src/lib.rs; printf '# d\n' > docs/api.md
    printf 'docs/api.md\tsrc/lib.rs\t2-4\t%s\n' "$(awk 'NR>=2&&NR<=4' src/lib.rs | git hash-object --stdin)" > .evergreen-manifest
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'no drift' && ok "manifest-region: pinned range matches" || bad "manifest-region: matches" "$out"

  ( cd "$t" && printf 'l1\nfn target() {\n  body\n}\nl5\nCHANGED\n' > src/lib.rs )   # edit OUTSIDE 2-4
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'no drift' && ok "manifest-region: edit outside range stays clean (the win)" || bad "manifest-region: outside-range" "$out"

  ( cd "$t" && printf 'l1\nfn target() {\n  NEWBODY\n}\nl5\nCHANGED\n' > src/lib.rs )  # edit INSIDE 2-4
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'src/lib.rs:2-4` changed' && ok "manifest-region: edit inside range -> needs_reverify" || bad "manifest-region: inside-range" "$out"

  ( cd "$t" && bash "$SCAN" --fix >/dev/null 2>&1 )
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'no drift' && grep -q '2-4' "$t/.evergreen-manifest" \
    && ok "manifest-region: --fix re-pins range (keeps range field)" || bad "manifest-region: --fix re-pin" "$out"

  ( cd "$t" && printf 'docs/api.md\tsrc/lib.rs\t2to4\tdead\n' >> .evergreen-manifest )
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'range `2to4`.*malformed' && ok "manifest-region: malformed range flagged" || bad "manifest-region: malformed range" "$out"
  rm -rf "$t"
}

# --- 15. Embed-from-source: match / drift / --fix / missing source -----------
test_embed(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs
    printf 'fn one() {}\nfn two() {}\nfn three() {}\n' > src/lib.rs
    { printf '# Doc\n<!-- evergreen:embed src/lib.rs:1-2 -->\n```rust\nfn one() {}\nfn two() {}\n```\n'; } > docs/g.md
    git add -A && git commit -qm init
  ) >/dev/null 2>&1

  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'no drift' && ok "embed: matching block is clean" || bad "embed: matching block is clean" "$out"

  ( cd "$t" && printf 'fn one() {}\nfn RENAMED() {}\nfn three() {}\n' > src/lib.rs )
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'has drifted from source' && ok "embed: drifted block is flagged" || bad "embed: drifted block is flagged" "$out"

  ( cd "$t" && bash "$SCAN" --fix >/dev/null 2>&1 )
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  if printf '%s' "$out" | grep -q 'no drift' && grep -q 'fn RENAMED' "$t/docs/g.md"; then
    ok "embed: --fix refreshes block from source, re-scan clean"
  else
    bad "embed: --fix refreshes block from source" "$out"
  fi

  ( cd "$t" && printf '<!-- evergreen:embed src/NOPE.rs:1-2 -->\n```\nx\n```\n' >> docs/g.md )
  out="$(cd "$t" && bash "$SCAN" 2>/dev/null)"
  printf '%s' "$out" | grep -q 'embed source `src/NOPE.rs` missing' && ok "embed: missing source range is flagged" || bad "embed: missing source range is flagged" "$out"
  rm -rf "$t"
}

# --- 14. Output formats: SARIF, freshness score, JSONL audit log -------------
test_outputs(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs; printf 'x\n' > src/a.py
    printf '# Doc\nSee `src/GONE.py` and `--ghost-flag`.\n' > docs/g.md   # two high findings
    git add -A && git commit -qm init
  ) >/dev/null 2>&1

  # SARIF valid + 2 results (use python3 if present, else structural).
  out="$(cd "$t" && bash "$SCAN" --sarif 2>/dev/null)"
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$out" | python3 -c 'import sys,json;d=json.load(sys.stdin);assert d["version"]=="2.1.0";assert len(d["runs"][0]["results"])==2' >/dev/null 2>&1 \
      && ok "sarif: valid 2.1.0 with 2 results" || bad "sarif: valid 2.1.0 with 2 results" "$out"
  else
    printf '%s' "$out" | grep -q '"version":"2.1.0"' && ok "sarif: emits 2.1.0 envelope" || bad "sarif: emits 2.1.0 envelope" "$out"
  fi

  # freshness_pct: 2 high -> 100 - 30 = 70, in json and human.
  out="$(cd "$t" && bash "$SCAN" --json 2>/dev/null)"
  printf '%s' "$out" | grep -q '"freshness_pct":70' && ok "score: json freshness_pct=70 for 2 high" || bad "score: json freshness_pct=70" "$out"
  out="$(cd "$t" && bash "$SCAN" --score 2>/dev/null)"
  printf '%s' "$out" | grep -q 'freshness: 70%' && ok "score: human freshness line" || bad "score: human freshness line" "$out"

  # JSONL log: one valid JSON object per finding, appended.
  ( cd "$t" && bash "$SCAN" --log audit.jsonl >/dev/null 2>&1 )
  local n; n="$(wc -l < "$t/audit.jsonl" | tr -d ' ')"
  if [ "$n" = 2 ] && { ! command -v python3 >/dev/null 2>&1 || python3 -c 'import sys,json;[json.loads(l) for l in open(sys.argv[1])]' "$t/audit.jsonl" >/dev/null 2>&1; }; then
    ok "log: appends one valid JSONL object per finding"
  else
    bad "log: appends one valid JSONL object per finding (lines=$n)"
  fi
  rm -rf "$t"

  # clean repo -> freshness 100
  t="$(newrepo)"
  ( cd "$t" || exit 1; mkdir -p src; printf 'x\n' > src/a.txt; git add -A && git commit -qm init ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" --json 2>/dev/null)"; rm -rf "$t"
  printf '%s' "$out" | grep -q '"freshness_pct":100' && ok "score: clean repo is 100" || bad "score: clean repo is 100" "$out"
}

# --- 13. Tab in a doc filename must not corrupt the TSV finding sink ----------
test_tab_filename(){
  local t out; t="$(newrepo)"
  ( cd "$t" || exit 1
    mkdir -p src docs; printf 'x\n' > src/a.py
    printf '# Doc\nSee `src/GONE.swift`.\n' > "$(printf 'docs/tab\tname.md')"
    git add -A && git commit -qm init
  ) >/dev/null 2>&1
  out="$(cd "$t" && bash "$SCAN" --json 2>/dev/null)"
  rm -rf "$t"
  # JSON valid and detail intact (the GONE.swift detail must not leak into another field).
  local valid=0
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$out" | python3 -c 'import sys,json;json.load(sys.stdin)' >/dev/null 2>&1 && valid=1
  else
    printf '%s' "$out" | grep -q '^{"findings":\[' && valid=1
  fi
  if [ "$valid" = 1 ] && printf '%s' "$out" | grep -q 'GONE.swift'; then
    ok "tab-filename: TSV sink not corrupted (valid JSON, detail intact)"
  else
    bad "tab-filename: TSV sink not corrupted" "$out"
  fi
}

# --- 20. Golden snapshot: full --json of a canonical multi-signal fixture -----
# LLM-free regression rubric — exact match against tests/golden/expected.json.
# Regenerate intentionally with UPDATE_GOLDEN=1 bash tests/run.sh.
build_golden_fixture(){
  ( cd "$1" || exit 1
    git init -q && git config user.email t@t && git config user.name t
    mkdir -p src docs
    printf 'use --real-flag\nREAL_VAR=1\nfn a() {}\nfn b() {}\n' > src/keep.rs
    { printf '# Guide\n'
      printf 'Missing path `src/GONE.rs`.\n'
      printf 'Flags `--real-flag` and `--ghost-flag`.\n'
      printf 'Env `REAL_VAR` and `GHOST_VAR`.\n'
      printf '<!-- evergreen:embed src/keep.rs:3-4 -->\n```rust\nfn a() {}\nfn DRIFTED() {}\n```\n'
    } > docs/g.md
    git add -A && git commit -qm init ) >/dev/null 2>&1
}
test_golden(){
  local t out; t="$(mktemp -d)"; build_golden_fixture "$t"
  out="$(cd "$t" && bash "$SCAN" --json 2>/dev/null)"; rm -rf "$t"
  if [ "${UPDATE_GOLDEN:-0}" = 1 ]; then printf '%s\n' "$out" > "$GOLDEN"; ok "golden: regenerated tests/golden/expected.json"; return; fi
  [ -f "$GOLDEN" ] || { bad "golden: tests/golden/expected.json missing (run UPDATE_GOLDEN=1)"; return; }
  if [ "$out" = "$(cat "$GOLDEN")" ]; then
    ok "golden: full --json matches the committed snapshot"
  else
    bad "golden: --json drifted from snapshot (UPDATE_GOLDEN=1 to refresh after intended changes)" "GOT: $out"
  fi
}

# --- 21b. prose-fix SAFETY gates — deterministic, via a stubbed `claude` -------
# No live model needed: stub claude to MISBEHAVE and prove a bad draft is never applied,
# and to behave and prove a clean draft is. Locks the safety guarantee permanently.
mk_prose_repo(){ local t; t="$(mktemp -d)"; ( cd "$t" || exit 1
  git init -q && git config user.email t@t && git config user.name t
  mkdir -p src docs; printf 'real\n' > src/real.py
  printf '# Setup\n\nRun `src/old_runner.py` to start.\n' > docs/setup.md
  git add -A && git commit -qm init ) >/dev/null 2>&1; printf '%s' "$t"; }
# mk_stub DRAFT-CMD [VERDICT]: a fake `claude` that answers the review-call with VERDICT
# (default PASS) and otherwise emits the draft via DRAFT-CMD.
mk_stub(){ local d v; d="$(mktemp -d)"; v="${2:-PASS}"
  { printf '#!/usr/bin/env bash\n'
    printf 'for a in "$@"; do case "$a" in *"PASS or FAIL"*) echo %s; exit 0;; esac; done\n' "$v"
    printf '%s\n' "$1"; } > "$d/claude"; chmod +x "$d/claude"; printf '%s' "$d"; }
test_prose_safety(){
  local t s before
  # (1) malicious: draft keeps the dead path + reviewer always PASS -> must REFUSE (det gate 1)
  t="$(mk_prose_repo)"; s="$(mk_stub 'printf "# Setup\n\nRun \`src/old_runner.py\` (gone) — see \`src/real.py\`.\n"')"
  before="$(cat "$t/docs/setup.md")"; ( cd "$t" && PATH="$s:$PATH" bash "$SCAN" --fix-prose >/dev/null 2>&1 )
  [ "$before" = "$(cat "$t/docs/setup.md")" ] && ok "prose-safety: draft keeping the dead path is refused (det gate 1)" || bad "prose-safety: dead-path draft applied!"
  rm -rf "$t" "$s"
  # (2) preamble injection + reviewer always PASS -> must REFUSE (det gate 2: no net new lines)
  t="$(mk_prose_repo)"; s="$(mk_stub 'printf "INJECTED\n# Setup\n\nRun \`src/real.py\` to start.\n"')"
  before="$(cat "$t/docs/setup.md")"; ( cd "$t" && PATH="$s:$PATH" bash "$SCAN" --fix-prose >/dev/null 2>&1 )
  [ "$before" = "$(cat "$t/docs/setup.md")" ] && ok "prose-safety: added-preamble draft is refused (det gate 2)" || bad "prose-safety: preamble applied!"
  rm -rf "$t" "$s"
  # (3) clean draft + reviewer PASS -> APPLIED, dead path gone
  t="$(mk_prose_repo)"; s="$(mk_stub 'printf "# Setup\n\nRun \`src/real.py\` to start.\n"')"
  ( cd "$t" && PATH="$s:$PATH" bash "$SCAN" --fix-prose >/dev/null 2>&1 )
  if ! grep -q 'old_runner.py' "$t/docs/setup.md" && grep -q 'src/real.py' "$t/docs/setup.md"; then ok "prose-safety: a clean validated draft IS applied"; else bad "prose-safety: clean draft not applied"; fi
  rm -rf "$t" "$s"
  # (4) clean draft but reviewer FAILs -> must NOT apply
  t="$(mk_prose_repo)"; s="$(mk_stub 'printf "# Setup\n\nRun \`src/real.py\` to start.\n"' FAIL)"
  before="$(cat "$t/docs/setup.md")"; ( cd "$t" && PATH="$s:$PATH" bash "$SCAN" --fix-prose >/dev/null 2>&1 )
  [ "$before" = "$(cat "$t/docs/setup.md")" ] && ok "prose-safety: review-gate FAIL blocks the edit" || bad "prose-safety: applied despite FAIL"
  rm -rf "$t" "$s"
}

# --- 21. LLM prose-fixer harness (opt-in; live model + claude CLI) -----------
# Default-skipped so the suite stays fast/deterministic. Run with:
#   EVERGREEN_LLM_TESTS=1 bash tests/run.sh
test_golden_prose(){
  if [ "${EVERGREEN_LLM_TESTS:-0}" != 1 ]; then ok "golden-prose: skipped (EVERGREEN_LLM_TESTS=1 to run; needs claude)"; return; fi
  if bash "$(dirname "$0")/golden-prose.sh" >/dev/null 2>&1; then ok "golden-prose: --fix-prose resolves drift, preserves prose, adds no drift"
  else bad "golden-prose: harness failed (see: bash tests/golden-prose.sh)"; fi
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
test_tab_filename
test_outputs
test_embed
test_manifest
test_manifest_region
test_coverage
test_coverage_methods
test_coverage_ast
test_coverage_deno
test_coverage_badge
test_fix_engine
test_edge_hardening
test_golden
test_prose_safety
test_golden_prose
test_args

echo "----------------------------------------"
echo "summary: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
