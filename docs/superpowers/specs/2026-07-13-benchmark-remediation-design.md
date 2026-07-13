# Parallel benchmark remediation design

**Status:** Approved direction: run label validity, detector quality, and public auditability as
concurrent workstreams. Reconcile only at the frozen-judge, publication, and release gates.

## Objective

Turn the 0.4.0 five-language benchmark into an auditable baseline, establish credible human label
evidence, and improve the judge to a practical per-language gate of precision, recall, and F1 at or
above 0.60. Do not spend another full paid run until offline replay, fixtures, and a small
adjudicated development canary justify it.

This work does not treat the current CoDoc labels as truth, call aggregate provenance fully
reproducible, or claim that benchmark infrastructure already improved end-user detection.

## Parallel workstreams

### A. Label validity

Build a standard-library audit tool and schema that generate deterministic blinded annotation
packets, validate independent human labels, select adjudication work, compute agreement and weighted
error statistics, and rescore frozen transcripts. Automation must never claim an annotator is human
or substitute LLM consensus for human judgment.

The initial risk-stratified audit contains:

- all 130 published nominal positives;
- all 264 current nominal false positives;
- 25 deterministic true-negative samples per language;
- the Rust abstention; and
- 20 discarded candidates per CoDoc language when the exact source pool can be recovered.

Two blinded humans label every item. A third human independently labels all disagreements,
uncertainties, and a fixed 10% agreement sample. Packets conceal heuristic labels, LLM votes,
Evergreen outcomes, and other annotators. Labels are `consistent`, `inconsistent`, or
`insufficient-context`; inconsistencies require category, claim, evidence, and rationale.

The sampled audit passes only with complete adjudication, no unresolved rows, overall Cohen's
kappa at least 0.70 with bootstrap lower bound at least 0.60, per-language kappa at least 0.60,
weighted label-error upper bound at most 5% overall and 10% per language, no census stratum above
5% error, and no more than 5% clearly usable discarded candidates excluded solely because truth
opposed the heuristic. Failure escalates to the complete recoverable 2,365-row source pool.

Missing historical source pools remain explicitly unverified. A sampled pass is
`human-audited`; only a complete census is `human-validated`.

### B. Detector quality

Separate provider stage collection from a pure versioned resolver. The legacy resolver remains
available as `v1` so every 0.4.0 decision can be replayed exactly without provider calls. Resolver
identity becomes artifact provenance.

Replace the duplicated prosecution-heavy prong set with three distinct roles:

1. strongest consistent reading;
2. direct contradiction search; and
3. neutral evidence-sufficiency review.

Structured evidence distinguishes `direct`, `delegated`, and `requires-unseen-code`. Only direct
proof may become drift. The latter two become semantic `unverified`, not infrastructure
abstentions. Every landed challenge enters the declared resolution path.

For Java, construct bounded label-blind context from the exact source revision: enclosing type,
direct same-repository callees, referenced constants and fields, source paths, and content hashes.
Use fixed depth and byte limits. Missing context produces `unverified`; it never licenses guessing.

Detector development may use current labels only as provisional diagnostics. Human audit outcomes
are split by repository into development and locked holdout groups. Prompt and resolver work may
use the development group only; the holdout stays hidden until the candidate judge is frozen.

Evaluation order is offline resolver replay, provider-free fixtures, a small adjudicated development
canary, then one full paid five-language run only if every development language clears the practical
0.60/0.60/0.60 gate. The final run also requires at least 99% infrastructure completion and reports
semantic unverified separately.

### C. Public auditability

Publish a five-file decision package for the evaluated 0.4.0 baseline, not the raw transcripts.
CoDocBench has no detectable license file, so public artifacts exclude code, documentation,
function bodies, and all free-form model reasoning.

Public rows retain stable ID, language, benchmark label/category, final status/verdict/category,
contested state, and structured snap/challenge/prong/escalation/blind-spot/synthesis outcomes. A
deterministic manifest binds evaluated release, commit and tree, provider/CLI/models/concurrency,
skill and judge identities, source and public hashes/sizes/row counts, dataset hashes, projection
version, and omitted fields.

Verification fails closed on provenance, hash, byte-count, row-count, historical Git identity,
dataset join, language/ID coverage, schema allowlist, symlink, size, free-form-text, or report parity
failure. The checked-in aggregate report must regenerate byte-for-byte, and offline rescoring must
remain compatible. CI runs this verifier.

The package covers retained evaluated rows only. It does not claim recovery of discarded
TypeScript, Rust, or Go candidates or their exact source revisions.

## Ownership and integration

Each workstream uses an isolated branch/worktree and owns its new modules and tests. The root agent
owns shared files: `AGENTS.md`, judge and artifact schemas, benchmark reports, release manifests,
and cross-workstream documentation. Agents exchange schema and provenance constraints directly.

Offline work runs concurrently. Publication, same-branch commits, and paid benchmark lanes remain
serialized because they share mutable state or safety-limited resources.

## Delivery sequence at shared gates

1. Merge independent tooling behind tests without changing the published 0.4.0 baseline.
2. Generate the redacted 0.4.0 decision package and verify report parity.
3. Generate blinded human packets. Stop automation at the human-judgment boundary.
4. Develop the resolver, evidence-sufficiency roles, and Java context concurrently against fixtures
   and provisional development data.
5. Incorporate completed human development labels, freeze the candidate judge, then reveal the
   locked holdout.
6. Run one crash-safe full paid matrix only after every cheap gate passes.
7. Integrate all three streams as one minor release, expected `0.5.0`; retain the old publication as
   the explicitly named `0.4.0 baseline`.

## Verification

Every stream supplies focused unit tests. Before integration run:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/hooks.sh
bash tests/action.sh
python3 eval/bench/run_bench.py --selftest
```

The publication verifier must also regenerate the checked-in report exactly. No full provider-backed
benchmark is part of ordinary CI.

## Non-goals

- Republishing unlicensed third-party code or documentation.
- Publishing free-form reviewer reasoning.
- Automating human judgments or adjudication.
- Optimizing prompts against the locked holdout.
- Treating semantic uncertainty as provider failure.
- Running repeated full paid matrices to search prompt space.
