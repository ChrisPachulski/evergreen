# External-format benchmark

The [`../`](../) eval seeds a whole fixture repo and runs a full winnow. This benchmark is the
other axis: **per-pair** code-vs-doc consistency, in the schema the research literature uses, so
evergreen's numbers sit next to published baselines.

## Why a stand-in dataset (read this first)

The obvious move is to run against DocPrism's or CASCADE's published sets. As of July 2026 **you
can't** — neither is released. DocPrism's artifact is an anonymized peer-review link that has
already expired (`{"error":"repository_expired"}`); CASCADE's says "distribute as open-source
after acceptance." So `dataset.jsonl` here is **evergreen's own hand-labeled set in their exact
schema** — 14 pairs, honestly small, author-written, every label double-checked (the CCISolver
paper found 45.67% of a popular benchmark's positive labels were wrong; a noisy label is worse
than no label). It is a stand-in that proves the harness and yields a real number today — not a
claim to be DocPrism's data.

The harness reads the DocPrism/CASCADE schema directly, so the day either releases, it's one flag:

```sh
python3 eval/bench/run_bench.py --dataset path/to/docprism.jsonl
# or: EVAL_DATASET=path/to/set.jsonl python3 eval/bench/run_bench.py
```

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

Numbers, with the DocPrism baseline for context, live in [`RESULTS.md`](RESULTS.md).
