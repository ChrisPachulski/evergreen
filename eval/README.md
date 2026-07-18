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

The per-pair benchmark ([`bench/`](bench/)) reduces each language to one confusion matrix. The
Evergreen 0.4.0 five-language run reached at least 99% coverage per language from one frozen
implementation and provider identity, but canonical IDs leaked label proxies to both the judge and
the pre-fix label screen. Its metrics are therefore contaminated and unverified, not detector-quality
evidence. [`bench/results-0.4.0.md`](bench/results-0.4.0.md) and the checked-in
[public decision package](bench/public/0.4.0/manifest.json) remain replayable historical records.
