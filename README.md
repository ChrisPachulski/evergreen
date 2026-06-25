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

1. **Deterministic engine** (`bin/evergreen-scan`, zero-LLM, any language): in-repo
   file paths a doc names that vanished, files git renamed/deleted that docs still
   cite, CLI flags and env/config keys a doc documents that no code uses, and opt-in
   runnable examples that exit nonzero. Fast, no false-negatives, no token cost.
2. **Model triage only on candidates** the engine surfaced — to *classify* severity,
   never to *detect*. Never send whole files to a model.
3. **Fix safe**: auto-propose diffs only for content 1:1 derivable from code
   (signatures, paths, endpoint/type/config tables). Never auto-rewrite prose/intent —
   flag it for a human.

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
bin/evergreen-scan --base origin/main      # human report
bin/evergreen-scan --json                  # machine output
bin/evergreen-scan --ci --fail-level high  # exit 2 on high-severity drift (CI/pre-commit)
bin/evergreen-scan --run-examples          # also execute trusted doc examples (see below)
bin/evergreen-scan --selftest              # built-in self-check
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

## Status

v0.1 — the deterministic spine, the skill, the hook, and the command are live and
self-tested. The model-triage, coverage-score, and safe-auto-fix layers are designed
(`docs/DESIGN.md`) and land next. Prior-art mining notes live under `.research/`.

## Credits

Techniques are credited to their source repos in `docs/DESIGN.md` and the
`.research/mining/` reports — kedge, docs-drift-check, e4we/doc-staleness,
interrogate, Jan-ARN/drift, doc-checks, axiom-graph, docfresh, docs-guardian, and
many more from the 309-repo survey. Built to pair with ponytail.

MIT.
