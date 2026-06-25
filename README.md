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
bin/evergreen-scan --fix                       # apply derivable fixes only (never prose)
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
add a `.evergreen-manifest` TSV line (`doc<TAB>source<TAB>blob-sha`, sha via
`git hash-object`); when the source content changes the doc is flagged
`needs_reverify` and `--fix` re-pins it.

`--coverage` is a heuristic, dependency-free doc-comment coverage for py/js/ts/go/rs
(regex, not a parser — it undercounts methods/nested items; a tree-sitter pass is the
upgrade path). `--coverage --fix` records a `.evergreen-coverage` baseline; under
`--ci`, dropping below `--fail-under` *or* below the baseline (the ratchet) exits 2.

## Status

v0.1 — the deterministic spine (six signals), doc-comment coverage with delta-gating,
the derivable-only `--fix` engine, SARIF/JSONL/freshness-score outputs, the skill, the
hook, and the command are live and self-tested (`tests/run.sh`). Model triage and the
prose-fix gate (temp-0 validator, PR output) are designed (`docs/DESIGN.md`) and land
next. Prior-art mining notes live under `.research/`.

## Credits

Techniques are credited to their source repos in `docs/DESIGN.md` and the
`.research/mining/` reports — kedge, docs-drift-check, e4we/doc-staleness,
interrogate, Jan-ARN/drift, doc-checks, axiom-graph, docfresh, docs-guardian, and
many more from the 309-repo survey. Built to pair with ponytail.

MIT.
