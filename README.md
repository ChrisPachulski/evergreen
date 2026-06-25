# evergreen

**The documentation-freshness companion to [ponytail](https://github.com/DietrichGebert/ponytail).**

Ponytail asks *"does this code need to exist?"*. Evergreen asks *"does this doc still
match the code?"* — and answers it **deterministically before it answers it with a
model**. A ride-along reflex for Claude Code (and any agent that reads skills) that
catches doc drift the cheapest way that works.

> Fresh means *true to the code right now*, not *recently edited*. A checker that
> cries wolf gets muted — so evergreen flags only what it can prove against the code.

## Why it exists

The doc-freshness niche is real but nascent — a survey of **309 GitHub repos** (164
directly related, **79 of them zero-star**) found dozens of clever-but-unknown
approaches and no mature, ponytail-grade companion. Evergreen synthesizes the best
accredited techniques from that survey into one ride-along skill. Full credit map in
[`docs/DESIGN.md`](docs/DESIGN.md).

## How it works — detect cheap → triage smart → fix safe

1. **Deterministic engine** (`bin/evergreen-scan`, zero-LLM, any language) — six
   signals: in-repo file paths a doc names that vanished; files git renamed/deleted
   that docs still cite; CLI flags and env/config keys a doc documents that no code
   uses; embed-from-source snippets that drifted from the source lines they pin;
   SHA-pinned manifest sources that changed since a doc was last verified; and opt-in
   runnable examples that exit nonzero. Fast, no false-negatives, no token cost.
2. **Model triage only on candidates** the engine surfaced — to *classify* severity,
   never to *detect*. Never send whole files to a model.
3. **Fix safe**: `--fix` applies only the derivable fixes (embed refresh from source,
   manifest re-pin, coverage baseline). Prose and intent are never rewritten — flagged
   for a human. (Temp-0 validator and PR output for prose fixes are roadmap.)

## Install (Claude Code)

```
/plugin marketplace add <this-repo>
/plugin install evergreen
```

Then it rides along, plus:
- `/evergreen:audit [base-ref]` — full freshness audit.
- A non-blocking **Stop-hook** nudge when your code changes leave a doc lying.

## Standalone (any repo, no Claude)

```sh
bin/evergreen-scan --base origin/main         # human report
bin/evergreen-scan --json                      # machine output (includes freshness_pct)
bin/evergreen-scan --sarif                     # SARIF 2.1.0 for GitHub code-scanning
bin/evergreen-scan --score                     # append a freshness_pct line
bin/evergreen-scan --log audit.jsonl           # append findings as a JSONL audit trail
bin/evergreen-scan --ci --fail-level high      # exit 2 on high-severity drift (CI/pre-commit)
bin/evergreen-scan --coverage --fail-under 80  # doc-comment coverage (py/js/ts/go/rs)
bin/evergreen-scan --coverage --badge          # write a shields.io coverage badge into README
bin/evergreen-scan --fix                       # apply derivable fixes only (never prose)
bin/evergreen-scan --fix-prose                 # LLM-fix dead-path prose, gated (needs claude CLI)
bin/evergreen-scan --run-examples              # also execute trusted doc examples (see below)
bin/evergreen-scan --selftest                  # built-in self-check
```

The engine refuses to run outside a git repository (exits 1) rather than report a
false "clean". Contract checks (CLI flags, env/config keys) only consider tokens written
in `` `inline code` `` — prose is never flagged. Per-repo tuning via `.evergreen.sh`
(`CODE_ROOTS`). Design specs, ADRs, roadmaps and CHANGELOG history are exempt by default
— they lead the code. A test suite lives at `tests/run.sh`.

**Runnable examples execute code, so they are off by default.** A fenced block runs only
when its info string contains `evergreen` (e.g. ```` ```bash evergreen ````) AND you pass
`--run-examples` — the Stop hook never passes it, so opening an untrusted repo can't
auto-run its README. Blocks run with a scrubbed env and scratch HOME; this is not a
sandbox, so only use `--run-examples` on docs you trust.

**Pin snippets so they can't drift.** Mark a fenced block with
`<!-- evergreen:embed path/to/src.rs:10-20 -->`; the block is checked against those
source lines and `--fix` rewrites it from source. For prose tied to a source file,
add a `.evergreen-manifest` TSV line — whole-file `doc<TAB>source<TAB>blob-sha` (sha via
`git hash-object`), or region-pinned `doc<TAB>source<TAB>Lstart-Lend<TAB>sha` to bind a
doc to just a source line range (edits elsewhere in the file won't trip it). When the
pinned content changes the doc is flagged `needs_reverify` and `--fix` re-pins it.

`--coverage` is doc-comment coverage for py/js/ts/go/rs, **parser-backed where the
toolchain exists** — every parser is a single-file syntactic parse (no import resolution).
Python uses the stdlib `ast` parser (with `python3`); JS/TS use `deno doc --json` (with
`deno` + `python3` to read its output); Go uses the stdlib `go/ast` parser (with `go`);
Rust uses `syn` (with `cargo`). The Go/Rust helpers in `bin/helpers/` build and cache on
first use under `$XDG_CACHE_HOME/evergreen`. Each falls back to regex when its toolchain
is absent, a file won't parse, or the first build fails (e.g. offline). `--coverage --fix`
records a `.evergreen-coverage` baseline; under
`--ci`, dropping below `--fail-under` *or* below the baseline (the ratchet) exits 2.
`--coverage --badge` writes/refreshes a shields.io badge between
`<!-- evergreen:badge:start -->`/`<!-- evergreen:badge:end -->` markers in README.md
(idempotent; with no markers it prints the badge to stderr so `--json`/`--sarif` stays
valid).

**`--fix-prose` is the opt-in LLM fixer** for dead references — a file path, CLI flag,
or env/config key the code no longer has (requires the `claude` CLI). It drafts a minimal
correction, then enforces two deterministic gates (the draft removes every stale token,
and adds no net lines) plus an independent review-call that must pass — before writing the
fix to the working tree and printing the diff (never committed). Anything it can't
validate is left as `needs_review`. A changed (not absent) signature and free-form
rationale have no deterministic anchor, so they stay flagged for a human.

## Status

v0.1 — the deterministic spine (six signals, with region-pinned manifests),
doc-comment coverage with delta-gating and a shields.io badge (Python via `ast` and
JS/TS via `deno doc` parser-backed, Go/Rust regex), the derivable-only `--fix` engine,
the opt-in `--fix-prose` LLM fixer for dead references (paths, flags, env keys),
SARIF/JSONL/freshness-score outputs, the skill, the hook, and the command are live and
self-tested (`tests/run.sh`, plus `tests/golden-prose.sh`). Model triage and broader
prose fixes are designed (`docs/DESIGN.md`) and land next.
Prior-art mining notes live under `.research/`.

## Credits

Techniques are credited to their source repos in `docs/DESIGN.md` and the
`.research/mining/` reports — kedge, docs-drift-check, e4we/doc-staleness,
interrogate, Jan-ARN/drift, doc-checks, axiom-graph, docfresh, docs-guardian, and
many more from the 309-repo survey. Built to pair with ponytail.

MIT.
