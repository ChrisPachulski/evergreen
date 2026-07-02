# Benchmark results

`run_bench.py` over `dataset.jsonl` (14 hand-labeled pairs in the DocPrism schema), 2026-07-02,
Claude Code CLI 2.1.197, scored deterministically from the run transcripts (`out/*.json`) with no
manual credit.

## Core set — evergreen's territory (consistent + direct-mismatch + over-promise, n=12)

| judge | precision | recall | accuracy | flag-rate | confusion | DocPrism baseline ¹ |
|---|---|---|---|---|---|---|
| Opus 4.8 | **1.00** | **1.00** | **1.00** | 0.50 ² | TP 6 · FP 0 · FN 0 · TN 6 | 0.62 precision @ 0.15 |
| Haiku 4.5 | 0.86 | **1.00** | 0.92 | 0.58 | TP 6 · FP 1 · FN 0 · TN 5 | — |

Opus caught every inconsistent pair and left every consistent one alone — zero false positives.
Haiku matched the recall but produced one false positive: it flagged `upload_retries`, a *consistent*
pair whose doc ("retrying up to 3 times") matches a 3-iteration loop. That's the borderline pair in
the set doing its job — separating a model that reads the loop from one that pattern-matches the
prose.

¹ DocPrism (arXiv:2511.00215): 0.62 precision at a 15% flag rate across Python/TypeScript/C++/Java,
no fine-tuning. **Not the same dataset** — this is context, not a head-to-head (see caveats).
² Flag-rate is ~0.5 because the set is deliberately balanced (6 of 12 core pairs are truly
inconsistent); DocPrism's 0.15 is over a natural corpus that is mostly consistent, so the two
flag-rates are not comparable.

## Under-promise — the deliberate asymmetry (n=2)

**Both models flagged 0/2 — as designed.** `greet_extra` (an undocumented optional param) and
`read_config_expand` (undocumented `~` expansion) are cases where the code does *more* than the doc
says. DocPrism labels these `inconsistent`; evergreen holds "code is truth, the doc is the claim,"
so undocumented extra behavior is informational, not drift. Both judges correctly called them
`consistent`. The scorer reports this separately from recall rather than dragging it down — the
asymmetry working, not a miss.

## Honest caveats

- **The external sets aren't runnable.** DocPrism's peer-review artifact has expired
  (`repository_expired`); CASCADE's isn't released. So `dataset.jsonl` is evergreen's own labeled set
  in their schema — real numbers, but n=14 and author-written. The moment either releases,
  `run_bench.py --dataset <path>` produces a true head-to-head.
- **Small n, balanced by hand.** 1.00 across 12 clean pairs says the ruleset handles unambiguous
  drift with zero false positives on Opus; it does not claim 100% on a large natural corpus.
- **One run per model.** Re-run with `EVAL_MODEL=` to see the spread. Numbers here are recomputed
  from the committed transcripts, so anyone can re-derive them without spending API calls.
