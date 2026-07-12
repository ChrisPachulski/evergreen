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

The current-judge publication records one clean implementation commit and tree in every compatible
artifact, together with Claude Code CLI `2.1.197`, strong model `claude-opus-4-8`, cheap model
`claude-sonnet-5`, and `EVAL_CONCURRENCY=8`. The final implementation commit is frozen before any
language starts. The report gate is declared before scoring: every language artifact must finish
and individually meet the threshold passed to `report.py`. A provider interruption is resumed only
when the validated atomic artifacts share that exact provenance; partial or incompatible matrices
are never promoted as current results.

The checked-in CASCADE conversion is derived from upstream commit
`4dc5a8d525c8967ea8dd11ae46cfe5834dbda156` under its MIT license. The upstream
`PaperEvaluation/dataset.zip` SHA-256 is
`dbf023fbe10869879680a33edf196f286c042789d85272523752602ce39b403c`; the resulting
`cascade-java.jsonl` has 885 rows (70 inconsistent / 815 consistent), is 712,905 bytes, and has
SHA-256 `1c322acf6bc02ae304c062f0d53306e6e9ebb0334bd133afd57940922892ae0b`.

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

The five-language publication is **not yet available**. Python, Java, TypeScript, Rust, and Go must
all individually clear the declared report coverage threshold before `results-current.md` is
generated and linked here. A provider session limit interrupted a preliminary run, and implementation
commits landed between language starts. Those diagnostic checkpoints have incompatible provenance
and will not be resumed or presented as results. All five languages require a fresh run from one
stabilized commit. Historical matrices from the prior judge are not current and must not be cited as
such.

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
