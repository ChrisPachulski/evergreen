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

The per-pair benchmark ([`bench/`](bench/)) sets evergreen next to the published peer in the same
regime — **DocPrism** (zero-shot, multi-language, no fine-tuning). Current judge, CoDocBench Python:

| System | Precision | Recall | F1 | Flag rate |
|---|---|---|---|---|
| **DocPrism** (arXiv:2511.00215) | 0.62 | — | — | ~0.15 |
| **evergreen** — natural 10/90 | 0.57 | 0.89 | 0.70 | 0.16 |
| **evergreen** — balanced 50/50 | 0.89 | 0.89 | 0.89 | 0.50 |

Precision *trails* the peer at a matched flag rate (0.57 vs 0.62); recall (0.89) is the strength —
DocPrism doesn't publish recall, so it's stated, not spun. Fine-tuned single-language SOTA reaches
F1 0.88–0.94, a different regime and out of scope. Java (CASCADE) and TypeScript/Rust/Go re-runs
against this judge are pending — [`bench/`](bench/) holds the full breakdown and prior-judge numbers.
