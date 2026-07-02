# Measured results

The harness in this directory, run against the seeded fixture: 10 planted drifts (two or more
per ladder rung), 8 true-claim decoys, 2 exempt docs. One run per model, 2026-07-01, Claude Code
CLI 2.1.197, prompt = this branch's SKILL body, scored by `score.py` with no manual credit.

| judge model | drift caught (recall) | decoy false positives | exempt docs honored | precision |
|---|---|---|---|---|
| Opus 4.8 | **10/10** | **0/8** | 2/2 left alone | 9/9 = **1.00** |
| Haiku 4.5 | 9/10 ¹ | 1/8 ² | 2/2 left alone | 8/9 = 0.89 |

¹ The scored miss (D8, "returns None" vs the code's KeyError) was in fact caught — Haiku merged
it into its snippet-signature flag and the strict token-matching scorer didn't credit the merge.
Scored as a miss anyway; the scorer doesn't bend for the model.
² The one decoy hit was a formatting error, not a false accusation: Haiku emitted a
certification (`"why": "CERTIFIED: config.py:4 …"`) mislabeled as a flag. Counted against it
anyway.

Beyond the answer key, the Opus run also did what the ruleset asks on claims the manifest never
covered: it marked two undecidable prose claims (`--retries` and `--verbose` are parsed but never
consumed by any upload code) `behavior-asserted — verify manually` instead of passing or guessing,
and silenced `git push --follow-tags` as a third-party contract.

Honest caveats: n=1 per model, and the fixture is small and written by evergreen's author — an
exam we could pass, published so you can re-run it and so regressions show:

```sh
bash eval/run.sh
EVAL_MODEL=claude-haiku-4-5-20251001 bash eval/run.sh
```

The per-pair companion benchmark, in the schema the research literature uses, lives in
[`bench/`](bench/). Its headline numbers come from wild, label-validated data at a **natural
10/90 class split**: on **CASCADE's released dataset** (885 execution-validated Java pairs,
arXiv:2604.19400) evergreen/Haiku scores F1 0.30 vs the Cascade tool's 0.28, and on a 332-pair
CoDocBench-derived Python set it holds recall 1.00 at 0.23 precision. The author-written
12-pair fixture there is a labeled sanity check, not a comparable result. DocPrism's 0.62
precision (arXiv:2511.00215) is quoted as the peer baseline; its own dataset artifact is still
dead, so that number is context, not same-data.
