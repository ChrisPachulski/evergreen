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

The raw result is a **confusion matrix** (see [Results](#results--how-it-compares)). Recall and
specificity read straight off it; precision doesn't, because it moves with the drift base rate — so
precision is also reported reweighted to a **natural 10/90** and a **balanced 50/50** split, medians
over 1000 resamples of the consistent class (CASCADE's protocol), so it lines up with published
baselines. Rerun any committed transcript without API calls:

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

Only Python has been re-scored on the corrected judge so far. Every reported rate comes from **one
confusion matrix** over the 332 validated CoDocBench Python pairs — 9 real drifts, 323 true claims
(`--rescore out/bench-codoc-py-*-trial-*.json`):

|                      | flagged | silent  |
|----------------------|---------|---------|
| **actual drift** (9)   | TP 8  | FN 1    |
| **actual true** (323)  | FP 16 | TN 307  |

Two rates fall straight out and **don't depend on how common drift is**:

- **Recall** = 8/9 = **0.89** — of the real drifts, it caught 8
- **Specificity** = 307/323 = **0.95** — of the true claims, it wrongly flagged 16 (the cry-wolf rate)

**Precision does depend on the drift base rate**, so it's reported at three priors rather than
cherry-picked — same 8 correct flags, different assumed mix of the class it's diluted against:

| Prior (drift : true) | Precision | F1 | what it is |
|---|---|---|---|
| raw corpus (~3 : 97) | 0.33 | 0.48 | this dataset's actual mix |
| 10 : 90 | 0.57 | 0.70 | CASCADE reporting convention |
| 50 : 50 | 0.89 | 0.89 | balanced |

**vs the peer:** DocPrism reports 0.62 precision @ ~15% flag rate. At a matched flag rate (the 10/90
line) evergreen trails at 0.57. DocPrism publishes no recall, so the 0.89 has nothing to beat — state
it, don't spin it. And precision here rests on just **9 positives**, so it's the soft, noisy axis;
recall and specificity are the solid ground.

### Pending re-run — prior judge, do NOT cite as current

CASCADE (885 Java pairs — 70 drift, 815 true) ran only on the *old* judge. Its matrix is here so
nothing is hidden, not as a current number:

|                       | flagged | silent  |
|-----------------------|---------|---------|
| **actual drift** (70)   | TP 23 | FN 47   |
| **actual true** (815)   | FP 69 | TN 746  |

Recall 23/70 = **0.33** — the weakest result in the whole suite, and precisely what the trial rebuild
targets (specificity 0.92, raw precision 0.25). TypeScript/Rust/Go re-runs against the current judge
aren't done. Treat multi-language as **unproven on the current judge** — not the ~0.8 the old
transcripts once showed.

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
