# Benchmark results

Scored deterministically from committed run transcripts (`out/*.json`) with no manual credit —
`python3 run_bench.py --rescore out/<file>.json` re-derives every number here without API calls.

**Protocol.** Real doc-drift is rare (~8% of functions in CASCADE's natural corpus), so balanced
50/50 sets overstate precision by the prevalence gap — CASCADE itself drops 0.88 → 0.39 precision
moving balanced → 10/90 (arXiv:2604.19400). We therefore report **precision · recall · F1 at a
natural 10/90 split as the headline**, taking medians over 1000 resamples of the consistent class
(CASCADE's method, mirrored so numbers line up), with the balanced split and flag-rate as
secondary lenses.

**Baseline regime.** The peer is **DocPrism** (arXiv:2511.00215): 0.62 precision at a 15% flag
rate across Python/TypeScript/C++/Java, zero fine-tuning, LLM-proves-each-finding — the regime
evergreen lives in. Fine-tuned single-language SOTA (CCISolver 89.54 F1, CARL-CCI ~0.88–0.94) is
trained on cleaned single-language data and is **out of scope** — a prompt ruleset doesn't play
in that regime, and we don't invite the comparison. Off-the-shelf zero-shot GPT-4 (~0.50
accuracy, high recall / low precision) is the floor the discipline is supposed to beat.

## Sanity fixture (n=12 core, author-written — NOT a comparable result)

`dataset.jsonl`: 14 hand-labeled pairs (12 core + 2 under-promise), 2026-07-02, Claude Code CLI
2.1.197. Author-written and balanced by construction, so it proves the harness and catches
regressions; it compares to nothing.

| judge | split | precision | recall | F1 | specificity | flag-rate |
|---|---|---|---|---|---|---|
| Opus 4.8 | **natural 10/90** ¹ | 1.00 | 1.00 | 1.00 | 1.00 | 0.10 |
| Opus 4.8 | balanced 50/50 | 1.00 | 1.00 | 1.00 | 1.00 | 0.50 |
| Haiku 4.5 | **natural 10/90** ¹ | **0.40** | 1.00 | 0.57 | 0.83 | 0.25 |
| Haiku 4.5 | balanced 50/50 | 0.86 | 1.00 | 0.92 | 0.83 | 0.58 |

¹ The fixture has only 6 consistent pairs, so the 10/90 split bootstraps them **with**
replacement to 54 — an estimate of natural-prevalence behavior, not a wild corpus. Haiku's one
false positive (borderline pair `upload_retries`) is the whole story of the table: harmless at
50/50 (0.86), it collapses precision to 0.40 at natural prevalence. That is why balanced numbers
must not be headlines.

Footnote for the record: this fixture's Opus row was previously reported as a headline
"1.00 precision / 1.00 recall." **Balanced sanity fixture, n=12, author-written — not a
comparable result.** Zero false positives across 6 consistent pairs bounds the fixture FPR, it
does not claim 1.00 on a natural corpus.

## Under-promise — the deliberate asymmetry (n=2)

**Both models flagged 0/2 — as designed.** `greet_extra` (an undocumented optional param) and
`read_config_expand` (undocumented `~` expansion) are cases where the code does *more* than the
doc says. DocPrism labels these `inconsistent`; evergreen holds "code is truth, the doc is the
claim," so undocumented extra behavior is informational, not drift. The scorer reports this
separately from recall rather than dragging it down — the asymmetry working, not a miss.

## Honest caveats

- **Small n, author-written fixture.** The moment a number matters, use the CASCADE section
  above, not the fixture.
- **One run per model.** Re-run with `EVAL_MODEL=` to see the spread. Numbers here are recomputed
  from the committed transcripts, so anyone can re-derive them without spending API calls.
- **DocPrism's own set is still unrunnable** (4open.science artifact returns
  `repository_expired`, re-checked 2026-07-02); its 0.62 is context from the paper, not a
  same-data head-to-head. The harness reads its schema, so the day it releases:
  `run_bench.py --dataset <path>`.
