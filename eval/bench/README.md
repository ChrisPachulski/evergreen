# External-format benchmark

The [`../`](../) eval seeds a whole fixture repo and runs a full winnow. This benchmark is the
other axis: **per-pair** code-vs-doc consistency, in the schema the research literature uses, so
evergreen's numbers sit next to published baselines.

## The datasets

**CASCADE** ([github.com/TobiasKiecker/CASCADE](https://github.com/TobiasKiecker/CASCADE), MIT)
is released and is the real test: 885 wild Java method/Javadoc pairs (70 inconsistent /
815 consistent), labels validated by developers' own Javadoc-fix commits. Convert and run:

```sh
git clone https://github.com/TobiasKiecker/CASCADE
unzip CASCADE/PaperEvaluation/dataset.zip -d cascade_dataset
python3 eval/bench/cascade_to_jsonl.py cascade_dataset > cascade.jsonl
EVAL_CONCURRENCY=8 python3 eval/bench/run_bench.py --dataset cascade.jsonl
```

**CoDocBench** ([github.com/kunpai/codocbench](https://github.com/kunpai/codocbench),
arXiv:2502.00519) supplies the wild *Python* set — 4,573 coupled code+docstring changes with no
drift labels, from which we derive candidates ((old doc, new code) = the doc that lagged;
(new doc, new code) = control) and then **validate every label with a three-LLM majority vote**
before scoring, reporting inter-annotator kappa:

```sh
git clone https://github.com/kunpai/codocbench
python3 eval/bench/codocbench_to_jsonl.py codocbench/dataset/codocbench.jsonl \
    --pos 40 --neg 360 --seed 0 > derived.jsonl
EVAL_CONCURRENCY=6 python3 eval/bench/validate_labels.py derived.jsonl --out validated.jsonl
EVAL_CONCURRENCY=8 python3 eval/bench/run_bench.py --dataset validated.jsonl
```

**DocPrism's** set is still not runnable — its `anonymous.4open.science/r/DocPrism-5746`
artifact returns `{"error":"not_connected"}` — so its published precision can't be re-measured
on the same data.

`dataset.jsonl` here is **evergreen's own hand-labeled sanity fixture in their exact schema** —
14 pairs, honestly small, author-written, every label double-checked (the CCISolver paper found
45.67% of a popular benchmark's positive labels were wrong; a noisy label is worse than no
label). It proves the harness and catches regressions; it is not a comparable result.

## Protocol

Metrics are reported at a **natural 10/90 split** (headline) and a balanced 50/50 split, each as
medians over 1000 resamples of the consistent class — CASCADE's own protocol, mirrored so the
numbers line up. Rerun any committed transcript without API calls:

```sh
python3 eval/bench/run_bench.py --rescore out/bench-default.json
```

## Results — how it compares

The point of the schema above is that evergreen's numbers land next to published peers, so here
they are. **DocPrism** (arXiv:2511.00215) is the honest comparison: zero-shot, multi-language, no
fine-tuning — evergreen's exact regime. Fine-tuned single-language systems reach F1 0.88–0.94 but
train on one language's labels; different regime, noted and out of scope.

### The peers

| System | Regime | Precision | Recall | F1 | Flag rate |
|---|---|---|---|---|---|
| Fine-tuned single-language SOTA | trained, 1 language | — | — | 0.88–0.94 | — |
| **DocPrism** (arXiv:2511.00215) | zero-shot, multi-language | 0.62 | — | — | ~0.15 |

### Evergreen — current judge (the trial rebuild)

Only Python has been re-scored on the corrected judge so far. CoDocBench Python, three-LLM–validated
labels, medians over 1000 resamples (`--rescore out/bench-codoc-py-*-trial-*.json`):

| Split | n (core) | Precision | Recall | F1 | Specificity | Flag rate |
|---|---|---|---|---|---|---|
| natural 10/90 (headline) | 50 | 0.57 | 0.89 | 0.70 | 0.93 | 0.16 |
| balanced 50/50 | 50 | 0.89 | 0.89 | 0.89 | 0.89 | 0.50 |
| held-out true claims | 282 | n/a | n/a | n/a | 0.95 | 0.05 |

The 282-pair held-out set is **all true claims — no seeded drift**, so precision/recall/F1 have no
positives to score (they're `0/0`, undefined). It's a pure false-alarm test: the only meaningful
numbers are specificity (269 correct silences / 282) and flag rate (13 false flags / 282 = 0.05).

**Read it straight:** the only apples-to-apples number is precision at a matched flag rate, and there
evergreen *trails* — 0.57 at a 0.16 flag rate vs DocPrism's 0.62 at ~0.15. DocPrism doesn't publish
recall, so evergreen's 0.89 recall has no peer number to beat; state it, don't spin it. The rebuild
moved Python from the prior judge's 0.54 / 0.78 / 0.64 (natural, n=332) to 0.57 / 0.89 / 0.70 —
recall was the target and it moved most; precision barely.

### Pending re-run — prior judge, do NOT cite as current

These ran on the *old* judge and are the reason for the rebuild. Re-runs against the trial judge
aren't done; the numbers are here only so nothing is hidden:

| Dataset | n | Split | Precision | Recall | F1 |
|---|---|---|---|---|---|
| CASCADE Java | 885 | natural 10/90 | 0.30 | 0.33 | 0.32 |
| CASCADE Java | 885 | balanced 50/50 | 0.79 | 0.33 | 0.46 |
| CoDocBench TS / Rust / Go | — | — | pending | pending | pending |

The prior judge's Java recall (0.33) is the weakest result in the whole suite and precisely what the
trial rebuild targets. Until CASCADE is re-scored, treat multi-language performance as **unproven on
the current judge** — not as the ~0.8 the old transcripts once showed.

## Schema

One JSON object per line:

| field | values |
|---|---|
| `id`, `func` | identifiers |
| `code`, `doc` | the function source and its documentation |
| `label` | `consistent` \| `inconsistent` |
| `category` | `null` (consistent) \| `direct-mismatch` \| `over-promise` \| `under-promise` |

The three inconsistency categories are DocPrism's.

## The under-promise asymmetry (deliberate)

Evergreen's creed is **code is truth, the doc is the claim**: a doc that *over-promises* or
*contradicts* the code is a finding; code that quietly does *more* than the doc says
(under-promise) is informational, not drift. DocPrism labels under-promise `inconsistent`.
So on those pairs evergreen "misses" **by design** — the scorer reports them **separately**
rather than dragging down recall, and names the asymmetry instead of hiding it.

## Run

```sh
python3 eval/bench/run_bench.py            # CLI default model
EVAL_MODEL=claude-haiku-4-5-20251001 python3 eval/bench/run_bench.py
python3 eval/bench/run_bench.py --selftest # prove the scoring math, no API calls
```

The run prints precision/recall/F1 at both splits. Current measured numbers and the peer
comparison are in [Results](#results--how-it-compares) above.
