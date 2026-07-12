# The eval — prove it or drop it, applied to evergreen itself

A skill that demands citations owes you numbers. This harness measures the ruleset against a
fixture repo with **seeded, known drift**: 10 planted lies (at least two per ladder rung), 8
true-claim decoys that must *not* be flagged, and 2 exempt docs (an ADR and a CHANGELOG) that
must be left alone.

## Layout

- `fixture/` — a small Python CLI (`shipit`) whose README and docs lie in specific, catalogued
  ways. The drift is the exam; don't fix it. (The repo's own reflex skips it via
  `.evergreen-ignore`.)
- `manifest.tsv` — the answer key: every seeded case with its kind (`drift | decoy | exempt`),
  doc file, matchable token, and ladder rung.
- `prompt.md` — the task given to the judged model (appended to the shipped SKILL body, so the
  eval always measures the ruleset as it currently is).
- `run.sh` — one measured run: `claude -p` winnows the fixture read-only, output is scored.
- `score.py` — grades a run against the manifest: recall over seeded drift, decoy false
  positives, exempt-doc violations, precision, and any unseeded flags for manual audit.

## Run it

```sh
bash eval/run.sh                      # default model
EVAL_MODEL=claude-haiku-4-5-20251001 bash eval/run.sh   # or pin one
```

The harness prints its own numbers on each run.

## How it compares

The per-pair benchmark ([`bench/`](bench/)) reduces each language to one confusion matrix, but no
five-language current-judge result is published yet. Python, Java, TypeScript, Rust, and Go must all
finish and individually clear the declared generated-report coverage threshold. The current run was
interrupted by a provider session limit, and implementation commits landed between language starts.
Those diagnostic checkpoints have incompatible provenance and will not be resumed or published. A
fresh run from one stabilized commit is required; partial matrices and historical prior-judge numbers
are intentionally not presented as current results. [`bench/`](bench/) records the run protocol and
dataset provenance.
