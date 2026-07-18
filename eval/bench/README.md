<h1 align="center">External-format benchmark</h1>

<p align="center">
  <em>A number you can't recompute is an opinion with decimal places.</em>
</p>

---

The [`../`](../) eval seeds a whole fixture repo and runs a full winnow. This benchmark is the
other axis: **per-pair** code-vs-doc consistency, in the schema the research literature uses, so
evergreen's numbers sit next to published baselines. The numbers come first; everything after
them is the provenance that earns them.

> **Integrity notice (2026-07-18).** The 0.4.0 artifacts remain byte-replayable historical
> records, but their metrics are **not valid performance evidence**. The frozen judge prompt
> exposed each canonical pair ID. CoDocBench-derived IDs encode candidate construction (`-old`
> for nominal inconsistent candidates and `-new` for nominal consistent candidates), while
> CASCADE `/cN` IDs identify heuristic consistent controls. The pre-fix three-model label screen
> exposed the same IDs and accepted partial batches. Treat every 0.4.0 metric and every later
> pre-fix run as contaminated and unverified until it is rerun with opaque IDs and fail-closed
> screening. The frozen report and artifacts remain unchanged.

## Results — how it compares

The point of running in the literature's schema is that evergreen's numbers land next to
published peers, so here they are. **DocPrism** (arXiv:2511.00215) is the honest comparison:
zero-shot, multi-language, no fine-tuning — evergreen's exact regime. It is regime-comparable,
not row-comparable: DocPrism's numbers come from its own corpus, not from these datasets, so the
table reads as "same rules, different exam" until a sealed same-row peer run exists. Fine-tuned
single-language systems reach F1 0.88–0.94 but train on one language's labels; different regime,
noted and out of scope.

The published tables report the observed dataset-base-rate matrices from **one frozen run**;
no variance across repeated runs is claimed. Decision replay is deterministic
(`replay.py --expect-stored` demands exact full-decision parity), and
`run_bench.py --rescore <artifact>` reprints the natural 10/90 and balanced 50/50 reweighted
splits for any artifact offline, so both reweighted views are derivable from the published
decisions rather than taken on faith. Label pedigree per dataset is stated in
[The datasets](#the-datasets) below; in particular, 743 of CASCADE's 815 consistent controls are
heuristic-selected rather than developer-validated, so the Java row measures agreement with
CASCADE's labeling, not semantic correctness.

### The peers

| System | Regime | Precision | Recall | F1 | Flag rate |
|---|---|---|---|---|---|
| Fine-tuned single-language SOTA | trained, 1 language | — | — | 0.88–0.94 | — |
| **DocPrism** (arXiv:2511.00215) | zero-shot, multi-language | 0.62 | — | — | ~0.15 |

### Historical 0.4.0 execution record (contaminated)

The five-language run passed its declared completion and artifact-provenance gates. The full
matrices and exact frozen record are in [`results-0.4.0.md`](results-0.4.0.md). Across 2,104
attempted pairs, 2,103 completed and one Rust pair abstained (99.95% overall completion); every
language individually met the predeclared 99% coverage threshold. The integrity notice above means
those matrices cannot support a detector-quality claim.

| Language | Completed | Abstained | Precision | Recall | F1 | Specificity |
|---|---:|---:|---:|---:|---:|---:|
| Java | 885 / 885 | 0 | 0.202 | 0.343 | 0.254 | 0.883 |
| Python | 332 / 332 | 0 | 0.129 | 1.000 | 0.228 | 0.811 |
| TypeScript | 284 / 284 | 0 | 0.213 | 1.000 | 0.352 | 0.780 |
| Rust | 303 / 304 | 1 | 0.464 | 0.684 | 0.553 | 0.947 |
| Go | 299 / 299 | 0 | 0.261 | 0.750 | 0.387 | 0.880 |

These are the recorded dataset-base-rate matrices, not a detector-quality baseline. The publication
still proves decision-level replay and coverage for this frozen run; label-proxy exposure prevents
using its precision, recall, or F1 as performance evidence.

## How a verdict happens

Under the current protocol, the judge is a general-purpose model reading cold — never fine-tuned,
never shown the benchmark label or canonical pair ID, and swappable by flag (`--provider`,
`--strong-model`). Canonical identity stays local for validated joins; the model receives the
constant ID `pair`. Consistency comes from machinery, not from the model behaving:

1. **No single opinion decides.** Every pair runs a small trial ([`trial.py`](trial.py)): a
   first-instinct read, a challenge arguing the opposite, three blind reviewers with assigned
   angles (defend the doc / prove it wrong / audit whether the evidence can settle it at all)
   who never see each other's answers, a missed-angle pass, and a synthesis referee. A cracked
   challenge, tie, missed angle, or thin evidence escalates; a unique direct-evidence plurality
   can stand without synthesis.
2. **No freeform verdicts.** Every response must match its stage's rigid schema. Verdict-bearing
   stages use fixed verdict/category menus and evidence fields. A malformed answer is retried a
   bounded number of times, then recorded as an abstention. Nothing is inferred from prose.
3. **Replay and folding are deterministic code.** Contested cases can receive a synthesis-model
   verdict; [`resolver.py`](resolver.py) validates and folds the stored stage outputs into the
   decision. Every stage output ships in the artifact, and `replay.py --expect-stored` re-derives
   the stored decision bit-for-bit with no model call.
4. **Everything that could drift is pinned.** Provider, CLI version, model names, the SHA-256
   of every judge source file, the dataset hashes, and the clean published repo commit are
   stamped into every artifact; the launcher refuses to start otherwise.
5. **Published numbers are one frozen run, disclosed as one run** — never a best-of-N. The
   run-to-run spread of an identical configuration is a stated open item, not a hidden one.

The model supplies judgment; the machinery supplies the discipline.

## The datasets

Two external corpora we can run, two published peers we can't, and one hand-labeled fixture —
with the pedigree of every label stated, not assumed.

**CASCADE** ([github.com/TobiasKiecker/CASCADE](https://github.com/TobiasKiecker/CASCADE), MIT)
is released and is the real test: 885 wild Java method/Javadoc pairs (70 inconsistent /
815 consistent). Label provenance is mixed: the 70 inconsistent pairs and their 72
developer-corrected counterparts in the released rows come from developers' own Javadoc-fix
commits, but the other 743 consistent controls were selected by what the CASCADE paper itself
calls a weaker heuristic (§4.2, arXiv:2604.19400) — nominal labels, not developer-validated
ground truth. Scoring against them measures agreement with CASCADE's labeling, not semantic
correctness; evergreen treats those as two separate claims and reports them separately.

<details>
<summary>Why 72 corrected rows when the paper's table says 71 — the release-vs-paper off-by-one</summary>

Upstream's mapping metadata explicitly names 71 pairs; the release carries one more corrected row
than the paper's table, the same release-vs-paper off-by-one visible in its 70/815 vs the paper's
71/814.

</details>

Convert and run:

```sh
git clone https://github.com/TobiasKiecker/CASCADE
unzip CASCADE/PaperEvaluation/dataset.zip -d cascade_dataset
python3 eval/bench/cascade_to_jsonl.py cascade_dataset > cascade.jsonl
python3 eval/bench/frozen_run.py --dataset cascade.jsonl \
  --archive-dir "$HOME/evergreen-benchmark-archive"
```

**CoDocBench** ([github.com/kunpai/codocbench](https://github.com/kunpai/codocbench),
arXiv:2502.00519) supplies the wild *Python* set — 4,573 coupled code+docstring changes with no
drift labels, from which we derive candidates ((old doc, new code) = the doc that lagged;
(new doc, new code) = control) and then **screen every candidate label with a three-LLM majority
vote** before scoring, reporting inter-annotator kappa. Current screens replace canonical IDs with
batch-local `item-NNNN` IDs and map them back only after the model returns. A timeout, nonzero exit,
unexpected or duplicate ID, or incomplete response aborts the screen; no partial-vote output is
promoted. The vote ledger is atomically checkpointed and bound to the exact dataset and screening
program bytes, three distinct model names, CLI version, and CLI-executable SHA-256. The executable
identity is checked around every batch and fully rehashed before output promotion. The three
screeners are Anthropic models
(`claude-fable-5`, `claude-opus-4-8`, `claude-sonnet-5` — per-pair votes are tracked in the
`*.votes.json` files), a different vendor from the `codex`/`gpt-5.6-sol` judge under evaluation,
so the screen is not the judge grading itself. The requested model names are pinned; provider-side
alias-to-snapshot resolution is external state and remains unverified unless the provider exposes
that identity.

The table below describes the **legacy contaminated screen only**. Its frozen vote files preserve
history, but canonical-ID exposure and partial batches mean the values are not label-validity
evidence and are not resumable by the current fail-closed screener:

| Language | Pairs | Unanimous | Fleiss' kappa |
|---|---:|---:|---:|
| Python | 400 | 90% | 0.66 |
| Go | 360 | 91% | 0.67 |
| Rust | 360 screened | 91%\* | 0.66 |
| TypeScript | 360 | 89% | 0.67 |

\* 10 of Rust's 360 screened pairs carry only two of three votes; 91% unanimous among fully-voted.

Both the historical and current protocols use LLM screening, not independent human validation:

```sh
git clone https://github.com/kunpai/codocbench
python3 eval/bench/codocbench_to_jsonl.py codocbench/dataset/codocbench.jsonl \
    --pos 40 --neg 360 --seed 0 > derived.jsonl
EVAL_CONCURRENCY=6 python3 eval/bench/validate_labels.py derived.jsonl --out validated.jsonl
python3 eval/bench/frozen_run.py --dataset validated.jsonl \
  --archive-dir "$HOME/evergreen-benchmark-archive"
```

New derivations preserve upstream `file_path` and the new version's `commit_sha`, falling back to
legacy top-level fields; `source_status` is `complete` only when owner, project, file, and commit
are nonempty. This does not retroactively repair the frozen 0.4.0 rows.

### Current v2 development freeze (screen complete; judge results pending)

The four cross-language candidate pools are frozen before any clean screening or judge outcome.
[`codocbench-v2-candidate-provenance.json`](codocbench-v2-candidate-provenance.json) binds the raw
or mined inputs, derivation parameters, generator bytes, pool hashes, repository lists, and the
four committed candidate split manifests. Repository-grouped development partitions were the only
rows authorized for the clean screen; the retained screened rows are the only rows authorized for
the pending judge run. Twenty rows per language that were opened in a pre-freeze blind sanity audit
are listed in
[`codocbench-v2-exposed-sanity-ids.json`](codocbench-v2-exposed-sanity-ids.json) and excluded from
the development partitions regardless of the audit verdict.

There is **no sealed holdout in this derivation**. Before the split was frozen, a discarded
screening protocol sent the full candidate pools to labelers with label-revealing canonical IDs.
Its outputs are excluded from this record and are not evidence, but that exposure makes the manifest's
`holdout`-named partition development-only too. It is excluded from the current study. A future
holdout must be mined and sealed from untouched rows.

| Language | Candidates | Eligible development | Dev projects | Excluded exposed partition | Projects there |
|---|---:|---:|---:|---:|---:|
| Python | 900 | 527 | 76 | 360 | 51 |
| TypeScript | 600 | 259 | 7 | 333 | 1 |
| Rust | 800 | 439 | 6 | 351 | 2 |
| Go | 800 | 623 | 4 | 161 | 4 |

The clean three-model screen retained 294 Python rows (55 inconsistent / 239 consistent), 139
TypeScript rows (18 / 121), 251 Rust rows (38 / 213), and 346 Go rows (54 / 292). The tracked
screened-dev manifests and selection receipts bind those exact external datasets and vote ledgers;
no v2 judge result is claimed yet.

This is a **development benchmark study**, not A-grade oracle evidence. These derived rows have no
executable oracle category (`category` is `null`), while the frozen grade contract requires five
oracle-kind cells and the source-pack program remains incomplete. After screening,
[`bind_subset.py`](bind_subset.py) checks every retained JSON row against the exact eligible parent,
recomputes the complete two-of-three retained set from the byte-bound vote ledger, and emits an
exact-byte manifest plus a selection receipt binding the parent, vote ledger, screening identity,
and output. Both files must be tracked and byte-identical to `HEAD`. Before spawning the judge,
[`frozen_run.py`](frozen_run.py) independently recomputes the retained set, validates both tracked
files, and requires the same dataset and receipt digests in artifact metadata.

For example, after screening the frozen Python development parent:

```sh
python3 -m eval.bench.bind_subset screened-dev.jsonl \
  --parent-dataset python-derived-v2-dev-eligible.jsonl \
  --parent-manifest eval/bench/codocbench-python-v2-dev-eligible-manifest.json \
  --vote-ledger screened-dev.votes.json --split dev \
  --out eval/bench/codocbench-python-v2-screened-dev-manifest.json \
  --receipt-out eval/bench/codocbench-python-v2-selection-receipt.json
# Commit and push both generated JSON files before the paid lane.
python3 eval/bench/frozen_run.py --dataset screened-dev.jsonl \
  --resolver v2 --context-protocol none --split dev \
  --split-manifest eval/bench/codocbench-python-v2-screened-dev-manifest.json \
  --selection-parent-dataset python-derived-v2-dev-eligible.jsonl \
  --selection-parent-manifest eval/bench/codocbench-python-v2-dev-eligible-manifest.json \
  --selection-vote-ledger screened-dev.votes.json \
  --selection-receipt eval/bench/codocbench-python-v2-selection-receipt.json \
  --archive-dir "$HOME/evergreen-benchmark-archive"
```

**DocPrism's** set is still not runnable — its `anonymous.4open.science/r/DocPrism-5746`
artifact returns `{"error":"not_connected"}` — so its published precision can't be re-measured
on the same data.

**DocChecker** (arXiv:2306.06347) can't be executed on this corpus either, checked 2026-07-14:
its only pretrained checkpoint was a hardcoded Google Drive file that now returns 404, no
HuggingFace or Zenodo mirror exists (`Fsoft-AIC` hosts 37 models, none of them DocChecker), and
running the untrained heads would be noise, not a result. Its same-corpus Java numbers survive
only as quoted by the CASCADE paper (arXiv:2604.19400): precision 0.10, F1 0.17 at 10/90.

`dataset.jsonl` here is **evergreen's own hand-labeled sanity fixture in their exact schema** —
14 pairs, honestly small, author-written, every label double-checked (the CCISolver paper found
45.67% of a popular benchmark's positive labels were wrong; a noisy label is worse than no
label). It proves the harness and catches regressions; it is not a comparable result.

## Protocol

The raw result is a **confusion matrix** (see [Results](#results--how-it-compares)). Recall and
specificity read straight off it; precision doesn't, because it moves with the drift base rate — so
precision is also reported reweighted to a **natural 10/90** and a **balanced 50/50** split, medians
over 1000 resamples of the consistent class (CASCADE's protocol), so it lines up with published
baselines. Rescore a committed public decision artifact without API calls:

```sh
python3 eval/bench/run_bench.py --rescore \
  eval/bench/public/0.4.0/bench-cascade-java-trial-codex-gpt-5.6-sol.json
```

Add `--ids <file>` (one pair id per line) to restrict the rescore to an explicit subset, such as a
locked holdout split once the label audit produces one. Every listed id must exist in the artifact;
unknown ids fail the command rather than shrinking the subset silently.

Resolver v2 lanes run on `cascade-java-v2-dev.jsonl` / `cascade-java-v2-holdout.jsonl`: the same
885 checked-in CASCADE rows plus regenerable git-window context where the method can be located
(a declared unavailability reason otherwise). The multi-megabyte augmented files live outside the
repository; the committed
[`cascade-java-v2-split-manifest.json`](cascade-java-v2-split-manifest.json) binds their exact
SHA-256s, and the split ordering was fixed by a digest committed before any v2 run existed. The
exact run declaration and the regeneration mechanics are in the fold below.

<details>
<summary>Regeneration mechanics — mirror derivation, HMAC split ordering, blob bounds, and the protocol ladder</summary>

Resolver v2 lanes run on `cascade-java-v2-dev.jsonl` / `cascade-java-v2-holdout.jsonl`: the same
885 checked-in CASCADE rows, each augmented with `java-git-window-v1` context derived from local
bare mirrors (`cascade_to_jsonl.py --context-protocol java-git-window-v1 --mirror-root …`; rows
whose method can't be located exactly carry a declared unavailability reason instead). A
successor protocol `java-git-window-v2` (strict-first ladder: the exact v1 match, then a
token-aware match bridging CASCADE's AST re-serialization; 810/885 available vs v1's 637/885,
with every v1 window reproduced byte-identically) and its own successor `java-git-window-v3`
(the byte-identical v2 method window plus bounded `callee-window` snippets — declarations of
called names resolved by `git grep` at the pair's commit, first-use order, capped per name and
by the global context byte budget) and `java-git-window-v4` (the v3 snippets byte-identically,
plus bounded `field-window` initializer snippets from the method's own file and one further
bounded hop of callee resolution inside first-hop callee windows) exist in code; the committed
manifest currently binds the v4-context datasets, whose availability rows are identical to
v2's. The mirror set is derivable
from the manifest itself: bare-clone every distinct `project` in
`cascade-java-v2-split-manifest.json` (16 GitHub repos) under `<mirror-root>/<owner>/<name>`. The
augmented files are multi-megabyte, and the grade inventory bounds every tracked blob at 1 MiB,
so they live outside the repository; only
[`cascade-java-v2-split-manifest.json`](cascade-java-v2-split-manifest.json) is committed, and it
binds both files' exact SHA-256s, as does every run artifact. The files are regenerable from the
upstream zip (hash below) plus mirrors at the pair ids' fixed commits. `make_split.py` generates
the repository-grouped 60/40 dev/holdout split and its schema-v1 manifest, ordered by HMAC-SHA256
keyed on the checked-in `cascade-java.jsonl` SHA-256 — a digest committed before any v2 run
existed, so the grouping was never tunable against v2 outcomes. This run split is not the human
label audit's split: audit splits additionally balance human-label cells and stay private. A v2
run declares `--resolver v2 --split-manifest eval/bench/cascade-java-v2-split-manifest.json
--split dev|holdout --context-protocol <the protocol the bound datasets carry>`.

</details>

Scoring is binary for every resolver: a completed pair is flagged or it is not. Resolver v2 rows
whose verdict lacks direct proof (`semantic_status: unverified`) score as **not flagged** —
consistent-side — in the raw matrix and both reweighted splits, and are additionally reported as
a diagnostic count. They are never excluded from the matrix, and abstentions keep their existing
outside-the-matrix handling.

Replay stored trial stages through their versioned decision policy, require exact full-decision
parity, and compare the strong snap diagnostically without model calls:

```sh
python3 eval/bench/replay.py out/bench-default.json \
  --resolver v1 --expect-stored --compare-snap
```

Successful replay output contains artifact hashes and parity counts; with `--compare-snap` it also
prints per-language v1/snap precision, recall, and F1. Successful output does not print pair text or
free-form model reasoning; a parity failure can include mismatched decision fields for diagnosis.

The Evergreen 0.4.0 execution record stores one clean implementation commit and tree in every compatible
artifact, together with provider `codex`, Codex CLI `0.144.1`, strong and cheap model
`gpt-5.6-sol`, and `EVAL_CONCURRENCY=4`. The implementation commit was frozen before any language
started. Language processes may be serialized to stay within local scratch-space limits. The report
gate is declared before scoring: every language artifact must finish and individually meet the
threshold passed to `report.py`. A provider interruption is resumed only when validated atomic
artifacts share that exact provider and provenance; partial, mixed-provider, or otherwise
incompatible matrices are never promoted as a release record. Those controls make the run
replayable; they do not cure the label-proxy contamination described above.

The frozen 0.4.0 report can be regenerated from its public publication set:

```sh
python3 eval/bench/report.py \
  --format v1 \
  eval/bench/public/0.4.0/bench-codocbench-validated-trial-codex-gpt-5.6-sol.json \
  eval/bench/public/0.4.0/bench-cascade-java-trial-codex-gpt-5.6-sol.json \
  eval/bench/public/0.4.0/bench-codocbench-ts-validated-trial-codex-gpt-5.6-sol.json \
  eval/bench/public/0.4.0/bench-codocbench-rust-validated-trial-codex-gpt-5.6-sol.json \
  eval/bench/public/0.4.0/bench-codocbench-go-validated-trial-codex-gpt-5.6-sol.json \
  --require-language Python \
  --require-language Java \
  --require-language typescript \
  --require-language rust \
  --require-language go \
  --coverage-threshold 0.99 \
  --markdown eval/bench/results-0.4.0.md
```

The declared set must exactly match the artifact languages. Each language must complete at least
99% of its pairs; abstentions remain visible and outside the confusion matrix instead of being
silently retried until they disappear. Generic or single-language reports use the same command
with their own explicit `--require-language` set; missing, duplicate, empty, or undeclared
languages fail publication.

The checked-in CASCADE conversion is derived from upstream commit
`4dc5a8d525c8967ea8dd11ae46cfe5834dbda156` under its MIT license. The upstream
`PaperEvaluation/dataset.zip` SHA-256 is
`dbf023fbe10869879680a33edf196f286c042789d85272523752602ce39b403c`; the resulting
`cascade-java.jsonl` has 885 rows (70 inconsistent / 815 consistent), is 712,905 bytes, and has
SHA-256 `1c322acf6bc02ae304c062f0d53306e6e9ebb0334bd133afd57940922892ae0b`.

## Public decision artifacts

The content-addressed [Evergreen 0.4.0 publication](public/0.4.0/manifest.json) contains every
retained pair's benchmark label, final decision, and structured trial outcomes. It deliberately
omits source code, documentation text, and free-form reviewer prose. Join a decision to its declared
dataset by `metadata.dataset.sha256` plus row `id`.

Verify the five files, historical provenance, dataset joins, and checked-in report without a model
or API call:

```sh
python3 eval/bench/publication.py verify \
  --manifest eval/bench/public/0.4.0/manifest.json \
  --repo . \
  --report eval/bench/results-0.4.0.md
```

Rescore any language directly:

```sh
python3 eval/bench/run_bench.py --rescore \
  eval/bench/public/0.4.0/bench-cascade-java-trial-codex-gpt-5.6-sol.json
```

The public artifact hashes, dataset joins, historical Git blobs, and report regeneration are
independently checkable. Each manifest `source.sha256` is instead a chain-of-custody record of the
private frozen artifact verified during export; CI cannot independently inspect a source artifact
that is intentionally not published.

### Publication and licensing boundary

CASCADE is attributed to its upstream MIT-licensed repository and frozen source commit above.
CoDocBench's upstream repository did not declare a detectable license when this publication was
prepared on 2026-07-13. The public decision package therefore does not duplicate source code,
docstrings, or free-form model explanations. This is a publication-scope constraint, not legal
advice or a claim that existing research inputs have been relicensed.

The package supports decision inspection and metric rescoring for the retained evaluated rows. It
does not reconstruct the full candidate-selection process: the TypeScript, Rust, and Go vote logs
do not contain the discarded candidates' source payloads or exact source revisions. Label validity,
selection validity, and decision quality remain separate claims; candidate-selection audit status
therefore remains unverified for those three discarded pools.

## Schema

One JSON object per line:

| field | values |
|---|---|
| `id`, `func` | identifiers |
| `code`, `doc` | the function source and its documentation |
| `label` | `consistent` \| `inconsistent` |
| `category` | `null` (uncategorized) \| `direct-mismatch` \| `over-promise` \| `under-promise` |

The three inconsistency categories are DocPrism's.

## The under-promise asymmetry (deliberate)

Evergreen's creed is **code is truth, the doc is the claim**: a doc that *over-promises* or
*contradicts* the code is a finding; code that quietly does *more* than the doc says
(under-promise) is informational, not drift. DocPrism labels under-promise `inconsistent`.
So on those pairs evergreen "misses" **by design** — the scorer reports them **separately**
rather than dragging down recall, and names the asymmetry instead of hiding it. The 0.4.0
publication retained zero under-promise pairs, so its tables show the carve-out's reporting
path (0/0 lines) without yet exercising it on live data.

## Run

```sh
python3 eval/bench/frozen_run.py \
  --dataset eval/bench/dataset.jsonl \
  --archive-dir "$HOME/evergreen-benchmark-archive" \
  --provider codex \
  --strong-model gpt-5.6-sol \
  --cheap-model gpt-5.6-sol \
  --concurrency 4
python3 eval/bench/run_bench.py --selftest # prove the scoring math, no API calls
```

`EVAL_PROVIDER` accepts `claude` or `codex`. A resumable run and a publication set use one provider
only; provider identity, CLI version, models, judge, repository state, and dataset hashes are bound
into every artifact. Screened v2 lanes additionally bind the tracked selection-receipt hash, which
commits the parent dataset/manifest, complete vote ledger, screen program, model names, and CLI
identity. Codex runs are ephemeral, read-only, schema-constrained, instructed not to use tools, and
abstain if a tool event appears.

`frozen_run.py` is mandatory for paid runs; `run_bench.py` requires the launcher's one-time inherited
handshake and refuses ordinary direct execution except for self-test and rescore. The launcher fails
before spending model calls unless the repository is clean, contains exactly one dataset language,
lives outside every managed plugin marketplace/cache path, and its exact commit is already a remote
ref tip. Split manifests, parent manifests, and selection receipts must be tracked files whose
working bytes exactly match that commit's `HEAD`. A user-global lock permits only one language lane.
Every runner checkpoint is mirrored
synchronously to the absolute external archive with its row count and SHA-256 in the filename.
Resume selects the highest untampered checkpoint whose complete metadata matches the new run; an
incompatible or corrupt live artifact is durably quarantined before restoration. During execution
the launcher aborts and terminates the model process group if the repository/archive inode or Git
identity changes, checkpoint archival fails, or either volume's free disk falls below the declared
minimum (8 GiB by default). The archive must live outside the repository, so replacing the checkout
cannot replace the backup.

The run prints precision/recall/F1 at both splits. Measured Evergreen numbers appear in
[Results](#results--how-it-compares) only after the declared publication gate passes.
