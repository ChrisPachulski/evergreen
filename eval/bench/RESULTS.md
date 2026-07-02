# Benchmark results

`run_bench.py` over `dataset.jsonl` (14 hand-labeled pairs in the DocPrism schema), 2026-07-02,
Claude Code CLI 2.1.197, judged by Opus 4.8 (1M), scored with no manual credit.

## Core set — evergreen's territory (consistent + direct-mismatch + over-promise, n=12)

| metric | evergreen (Opus 4.8) | DocPrism baseline ¹ |
|---|---|---|
| precision | **1.00** | 0.62 |
| recall | **1.00** | — |
| accuracy | **1.00** | — |
| flag-rate | 0.50 ² | 0.15 |
| confusion | TP 6 · FP 0 · FN 6→0 · TN 6 | — |

Every genuinely-inconsistent pair caught, every consistent pair left alone, zero false positives.

¹ DocPrism (arXiv:2511.00215): 0.62 precision at a 15% flag rate across Python/TypeScript/C++/Java,
no fine-tuning. **Not the same dataset** — see the caveat below; this is context, not a head-to-head.
² Flag-rate is higher here because the set is deliberately balanced (6 of 12 core pairs are truly
inconsistent); DocPrism's 15% is over a natural corpus that is mostly consistent.

## Under-promise — the deliberate asymmetry (n=2)

**Flagged 0/2 — as designed.** Both `greet_extra` (an undocumented optional param) and
`read_config_expand` (undocumented `~` expansion) are cases where the code does *more* than the doc
says. DocPrism labels these `inconsistent`; evergreen treats "code is truth, the doc is the claim"
as doctrine, so undocumented extra behavior is informational, not drift. The judge correctly called
both `consistent`. This is the asymmetry working, not a miss — which is why the scorer reports it
separately from recall.

## Honest caveats

- **The external sets aren't runnable.** DocPrism's peer-review artifact has expired
  (`repository_expired`); CASCADE's isn't released. So this is evergreen's own labeled set in their
  schema — real numbers, but n=14 and author-written. The moment either releases,
  `run_bench.py --dataset <path>` produces a true head-to-head.
- **Small n, balanced by hand.** 1.00/1.00 on 12 clean pairs says the ruleset handles unambiguous
  drift with zero false positives; it does not claim 100% on a large natural corpus.
- **One judge, one run.** Re-run with `EVAL_MODEL=` to see the spread; expect Haiku to trade a
  little recall for speed, as in the fixture eval.
