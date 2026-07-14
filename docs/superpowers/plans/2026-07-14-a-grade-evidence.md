# Evidence-Earned A Grades Implementation Plan

> **For agentic workers:** Use isolated worktrees for independent waves, test-driven development
> for every behavior change, and independent specification plus quality review before integration.

**Goal:** Make every internally controllable Evergreen grade a deterministic result of exact,
committed evidence; earn those grades on a five-language executable-oracle holdout; compare peers
on identical rows; and apply the passing release to both Claude and Codex.

**Architecture:** Add one standard-library grade verifier over a frozen policy and evidence package.
Build a separate executable-oracle corpus whose labels are derived from bounded harness outcomes,
not human or model assertions. Reuse the current benchmark runner, metrics, publication, receipt,
and host transaction boundaries. Keep development and holdout repositories disjoint, freeze all
candidate and peer identities before one holdout run, and release only if every category recomputes
to A.

**Design:** `docs/superpowers/specs/2026-07-14-a-grade-evidence-design.md`

## Global constraints

- Never accept a stored letter grade, pass boolean, free-form label, or self-attestation as evidence.
- Human and multi-model reviews are supplementary only. The internal detector grade is mechanically
  adjudicated and offline-rerunnable.
- Five languages are mandatory: Python, Java, TypeScript, Rust, and Go.
- Holdout requires at least 100 positive and 100 negative decisions per language, at least 10
  repositories per language, and no repository above 20% of a language holdout.
- Per-language holdout thresholds are provider completion, decision coverage `>= .99`; precision,
  recall, and F1 `>= .80`; specificity `>= .98`; lower repository-clustered 95% bounds for
  prevalence-adjusted precision, recall, and F1 `>= .70`; zero silently omitted rows.
- The grade uses metrics adjusted to 10% drift prevalence and publishes balanced metrics, raw
  confusion counts, and two-sided 95% intervals as supporting evidence.
- Repository groups, all derivatives, and all paired positive/negative rows remain in one split.
- Holdout content is unavailable during tuning. A failed holdout starts a new versioned split.
- A peer receives identical IDs, code, documentation, and labels. Unsupported languages are
  explicit `not-applicable`; missing applicable rows fail.
- No new runtime dependency is added to Evergreen. Corpus construction may use pinned language
  toolchains already present in CI, but verification remains network-free.
- Keep `0.4.0` historical. Do not bump to `0.5.0`, install, merge, push, or publish until every
  internal category passes. External publication and adoption remain `unverified` without direct
  evidence.
- The root integrator owns policy, public evidence, release metadata, canonical agent surfaces, and
  final grade reports.
- A trusted prior verifier checkout evaluates the untrusted candidate. The first verifier commit is
  bootstrap-only and cannot award itself.

## Wave 1: Frozen grade policy and verifier

### Task 1: Define the policy and fail-closed grade core

**Files:**

- Create: `evergreen/grade.py`
- Create: `eval/grade-policy-v1.json`
- Create: `tests/test_grade.py`

The policy has the closed top-level fields `schema_version`, `kind`, `policy_id`,
`required_categories`, `category_gates`, `required_languages`, `artifact_roles`, `detector`,
`required_command_ids`, `forbidden_path_rules`, `external_state_names`, and `limits`. Category IDs
are exactly `detector_quality`, `same_corpus_comparison`, `trust_security`,
`claude_self_application`, `codex_self_application`, `documentation_release_honesty`,
`reproducibility_ci`, and `cleanup`. Trusted verifier code independently enforces v1 threshold
floors so a candidate cannot lower its checked-in policy.

**RED:** Add tests proving:

- the policy contains exactly the eight design categories and five canonical languages;
- thresholds are loaded from policy only, never from evidence;
- evidence containing `grade`, `passed`, `success`, or category-specific threshold overrides is
  rejected rather than ignored;
- duplicate JSON keys, non-finite values, unknown/missing categories, missing languages, dropped
  rows, stale commit identities, and incomplete peer applicability fail closed;
- raw detector counts recompute coverage, precision, recall, F1, specificity, and the 10% prevalence
  matrix deterministically;
- one failed predicate yields `not-earned` only for its category and prevents overall A;
- external states remain ungraded and cannot affect internal grade calculation;
- deterministic input yields byte-identical JSON.
- the manifest distinguishes the frozen `subject` commit/tree from the runtime-captured later
  `evidence_head`; only allowlisted evidence/report paths may differ, while all executable candidate
  bytes remain identical to `subject`;
- a manifest never contains its own `evidence_head`, and the derived grade receipt is runtime output
  rather than a committed self-hash.

Run and observe the expected missing-module failure:

```sh
python3 -m unittest tests.test_grade
```

**GREEN:** Implement immutable policy loading, exact category validation, raw-count metric
recomputation, reason collection, and deterministic receipt generation. Use `json` with an
`object_pairs_hook` duplicate-key check and `math.isfinite`; add no schema dependency.

```sh
python3 -m unittest tests.test_grade
```

**Commit:** `feat(grade): add fail-closed A-grade verifier`

### Task 2: Expose bounded grade verification through the CLI

**Files:**

- Modify: `bin/evergreen`
- Modify: `evergreen/grade.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_grade.py`

**RED:** Add CLI tests for:

```text
evergreen grade verify --repo PATH --manifest PATH [--json]
```

Assert exact help, deterministic human/JSON parity, exit `0` only for overall A, exit `2` for
invalid/unsafe or valid-but-not-earned evidence with stable reason codes, and exit `1` for bounded
operational failure. Prove traversal, symlink,
non-regular, oversized, dirty, non-HEAD, and path-swap evidence is refused. Prove no subprocess,
network, provider, installer, or repository write occurs.

**GREEN:** Reuse the receipt module's bounded Git/HEAD and regular-file patterns through a small
shared helper only if extraction reduces code. Do not duplicate the synthetic Git snapshot engine.
Reject award/export/threshold/skip/waive/arbitrary-command flags. Bind the trusted verifier commit,
tree, and artifact hash in every result. This bootstrap commit is not eligible to grade itself.

```sh
python3 -m unittest tests.test_grade tests.test_cli
```

**Commit:** `feat(cli): verify evidence-earned grades`

## Wave 2: Executable-oracle corpus

### Task 3: Define and validate oracle seeds and derived rows

**Files:**

- Create: `eval/oracle/schema-v1.json`
- Create: `eval/oracle/oracle.py`
- Create: `tests/test_oracle.py`

**RED:** Add fixtures for all five languages and tests proving:

- a seed binds project, source origin/commit/license, language, code, documentation template,
  harness argv, expected observable, oracle kind, and content hashes;
- labels are derived only as `consistent` when the fixed documentation matches observed behavior or
  `inconsistent` after one versioned code mutation preserves parsing/compilation but produces the
  structured oracle mismatch;
- no input field can directly supply a label or verdict;
- harness argv is bounded, shell-free, language-allowlisted, network-free, and runs only in a
  disposable copied fixture tree with a timeout and bounded output;
- source and derivative bytes are hash-bound; one changed byte, ambiguous observable, compile
  failure, timeout, extra output, unknown mutation, or cleanup failure invalidates the package;
- every source row and its derivatives share a group ID.

**GREEN:** Implement the smallest common oracle around exact process stdout/exit observations.
Support versioned `return-value`, `raises`, `default-value`, `cardinality`, and `state-change`
observables using per-seed harnesses rather than language-specific semantic parsers. Reuse the
existing command safety vocabulary where applicable, but use a stricter explicit executable
allowlist for oracle harnesses. Run only inside pinned digest-addressed sandboxes with read-only
inputs, tmpfs scratch, unprivileged UID, no capabilities/network, bounded CPU/memory/PIDs/output/time,
and process-group termination. Do not reuse `eval/bench/prove.py` as the security boundary.

```sh
python3 -m unittest tests.test_oracle tests.test_execution_policy
```

**Commit:** `feat(eval): add executable documentation oracles`

### Task 4: Build repository-grouped development and locked holdout packages

**Files:**

- Create: `eval/oracle/build.py`
- Create: `eval/oracle/split.py`
- Create: `eval/oracle/similarity-policy-v1.json`
- Create: `tests/test_oracle_build.py`
- Modify: `eval/bench/split_manifest.py`
- Modify: `tests/test_bench_split_manifest.py`

**RED:** Prove:

- deterministic generation from a source manifest produces byte-identical rows regardless of
  source order;
- keyed group hashing keeps repositories, seeds, and derivatives together;
- public split output is ID-only and binds exact private package hashes without exposing code,
  docs, labels, mutation, observables, or the split key;
- a package below any language/repository/class minimum fails;
- imbalance above the declared maximum, duplicate IDs, project leakage, or one repository above
  20% fails;
- development selection opens only development rows; holdout paths are not readable by the
  development command;
- fork, mirror, vendored, exact, normalized-token, structural, and fuzzy duplicates cannot cross
  splits or overlap prompts, examples, tests, fixtures, and prior benchmark corpora;
- `split_manifest.py` remains backward compatible with the frozen 0.4.0 benchmark.

**GREEN:** Extend the existing split validator rather than create a competing manifest contract.
Generate external private packages with exclusive creation and owner-only modes; write public
manifests atomically.

The frozen similarity policy requires explicit `lineage_id` values and never infers lineage. Its
five-language lexer drops comments, preserves keywords/operators/punctuation/identifiers, maps
numbers to `<num>` and string/character contents to `<str>`, and maps non-keyword identifiers to
`<id>` for structural fingerprints. Documentation uses lowercase ASCII word tokens with numeric
normalization. Scan code and documentation independently. Fuzzy comparison uses Jaccard similarity
over 5-token shingles at `>= 0.85`, minimum 20 tokens per field; shorter fields still receive exact,
normalized-token, and structural equality checks. Bind the policy hash into package and split
manifests and fail closed on tokenizer ambiguity or policy drift.

```sh
python3 -m unittest tests.test_oracle_build tests.test_bench_split_manifest
```

**Commit:** `feat(eval): lock oracle development and holdout splits`

### Task 5: Curate and validate the five-language source pack

**Files:**

- Create: `eval/oracle/sources/manifest.json`
- Create: `eval/oracle/sources/{python,java,typescript,rust,go}/...`
- Create: `eval/oracle/README.md`
- Modify: `.github/workflows/test.yml`
- Modify: `tests/test_oracle_build.py`

Use hash-pinned, license-compatible public sources. Each retained seed must be independently
runnable without network access after checkout and must record the exact origin, commit, license,
extraction, and harness. Prefer small pure functions and existing examples; do not vendor package
managers, build caches, repositories, or unrelated source trees.

Build at least 10 source-project groups and 250 mechanically decidable seed claims per language.
Each seed produces a baseline consistent row, a semantic-no-op code control that remains consistent,
and one lagging-documentation row from a code mutation. The private holdout must clear the
100-positive/100-negative requirement after repository grouping.

Add a pinned TypeScript compiler and CI jobs that install or select explicit Python,
Node/TypeScript, JDK, Rust, and Go versions and run oracle validation without network after setup.
The macOS Java executable may be discovered via `JAVA_HOME` or Homebrew only during local
development; committed commands use the CI-provided JDK. Separate trusted Linux sandbox
regeneration from macOS/Linux offline content verification.

```sh
python3 -m eval.oracle.build validate --manifest eval/oracle/sources/manifest.json
python3 -m unittest tests.test_oracle tests.test_oracle_build
```

**Commit:** `eval: add five-language executable oracle sources`

## Wave 3: Same-corpus peer protocol

### Task 6: Freeze peer applicability and identical-row adapters

**Files:**

- Create: `eval/peers.py`
- Create: `eval/peers-v1.json`
- Create: `tests/test_peers.py`
- Modify: `eval/bench/frozen_run.py`
- Modify: `eval/bench/report.py`

**RED:** Prove:

- the peer manifest has exact source/version/config hashes and a five-language applicability matrix;
- the required single-pass base-model peer is always present;
- each applicable peer consumes the exact holdout ID-set hash and returns exactly one bounded result
  per ID;
- unsupported languages require explicit `not-applicable` with a reason;
- missing, duplicate, extra, reordered-with-changed-content, label-aware, or Evergreen-outcome-aware
  peer input fails;
- peer metrics are recomputed from raw decisions against the same oracle labels;
- comparison completeness is graded independently from whether Evergreen wins.

**GREEN:** Add a provider-neutral direct-classification peer lane to the existing frozen runner and
an adapter protocol for locally runnable open peers. Do not reimplement peers or silently translate
unsupported inputs. Freeze the initially runnable peer set only after source/version verification.
Strip project/mutation identity from model inputs and replace row IDs with opaque per-run IDs. Holdout
logs expose aggregate sealed counts only; they never print true labels, correctness, project names,
or mutation identities.

```sh
python3 -m unittest tests.test_peers tests.test_bench_report tests.test_frozen_run
```

**Commit:** `feat(eval): compare peers on identical oracle rows`

## Wave 4: Ownership-aware self-application

### Task 7: Support legitimate managed symlink roots without weakening ownership

**Files:**

- Modify: `evergreen/hosts.py`
- Modify: `evergreen/host_snapshot.py`
- Modify: `evergreen/host_types.py` only if the existing types cannot represent resolved roots
- Modify: `tests/test_hosts.py`
- Modify: `tests/test_host_snapshot.py`
- Modify neighboring transaction/recovery tests only when their contract changes

**RED:** Reproduce the current Claude/Codex failures in temporary homes. Add tests proving:

- a host root symlink to a user-owned, non-world-writable directory with a valid ownership record
  can be upgraded transactionally;
- a symlink without ownership proof, outside the home/config boundary, through a world-writable
  ancestor, with an ownership mismatch, cycle, path swap, or changed resolved destination is refused;
- stale Evergreen-owned skill links and instruction blocks migrate to the canonical source;
- unrelated user content is never overwritten or removed;
- dry-run is byte/mode/metadata preserving;
- rollback restores both lexical and resolved paths after an injected failure;
- doctor reports exact source version and canonical content hashes, not merely a target path.
- `doctor --host all` reports independent evidence for both hosts even when one is invalid.

**GREEN:** Resolve and snapshot the managed root once under the transaction lock, bind lexical and
resolved identities through preflight/apply, and reuse the existing ownership record. Do not allow a
symlink alone to establish ownership.

```sh
python3 -m unittest tests.test_hosts tests.test_host_snapshot tests.test_host_transaction tests.test_host_recovery
```

**Commit:** `fix(hosts): migrate owned symlinked installations safely`

### Task 8: Add self-application evidence to the grade package

**Files:**

- Modify: `evergreen/grade.py`
- Modify: `evergreen/hosts.py`
- Modify: `bin/evergreen`
- Modify: `tests/test_grade.py`
- Modify: `tests/test_cli.py`

**RED:** Require raw host evidence for Claude and Codex separately: canonical release/version, active
skill/command hashes, ownership record hash, instruction-block hash, resolved host root, doctor
result, fresh discovery result, and uninstall dry-run ownership set. Prove one stale cache/link or a
shared self-attested boolean prevents that host's A.

**GREEN:** Add a read-only JSON doctor mode or bounded host-evidence collector. The grade verifier
recomputes host grades from exact hashes and owned path sets.

```sh
python3 -m unittest tests.test_grade tests.test_hosts tests.test_cli
```

**Commit:** `feat(hosts): prove active Claude and Codex alignment`

## Wave 5: Candidate, holdout, and release

### Task 9: Produce a bounded development candidate

**Files:**

- Modify only files justified by development-set failures, typically:
  `skills/evergreen/SKILL.md`, `skills/evergreen/DIGEST.md`, `AGENTS.md`,
  `eval/bench/trial.py`, `eval/bench/resolver.py`, and their focused tests.
- Create external development artifacts; do not commit raw provider transcripts.

Before any provider call, verify all offline gates, exact development selection, clean candidate
commit, provider/model availability, and the global run lock. Run one small canary per language.
Then run the bounded development set. Diagnose only aggregate and development-row evidence. For
each change: add a failing fixture first, change one policy behavior, synchronize agent surfaces,
commit, and rerun the bounded development gate.

Do not proceed until every development language clears the final holdout thresholds with at least
the same row/class minimums.

**Commit:** one focused commit per proven detector change; final freeze commit
`eval: freeze A-grade detector candidate`

### Task 10: Freeze and run the untouched holdout and peers once

Verify and record before reveal:

- candidate commit/tree and clean receipt;
- policy, oracle, split, dataset, prompt, resolver, provider/model/runtime, and peer hashes;
- inaccessible holdout before freeze;
- CI-equivalent offline gates passing.

Mount/select the private holdout, run the five Evergreen lanes serially under the global run lock,
then run every applicable peer on the identical IDs. Generate raw result hashes, public redacted
decision evidence, metrics, intervals, peer comparison, and the grade receipt without editing the
candidate.

If any detector or comparison predicate fails, preserve only aggregate failure evidence, keep
`0.4.0` current, do not inspect row reasoning for tuning, and create a new split version before the
next attempt.

If they pass, commit the bounded public evidence package:

```text
eval/grade/public/0.5.0/policy.json
eval/grade/public/0.5.0/evidence.json
eval/grade/public/0.5.0/report.md
```

The trusted verifier captures `evidence_head` and emits the final grade receipt at runtime. Do not
commit a self-referential grade receipt. Verify that `subject..evidence_head` changes only the
allowlisted evidence/report paths and that all executable subject bytes remain exact.

**Commit:** `eval: publish evidence-earned A-grade receipt`

### Task 11: Put every proposed A on trial

Dispatch independent blind reviewers for:

- detector/oracle construct validity and leakage;
- same-corpus peer completeness;
- trust/security correctness;
- Claude/Codex active-install proof;
- documentation/release claims;
- reproducibility/CI and cleanup.

For each category use the verdict-on-trial sequence: snap, strongest challenge, three blind reads,
blind-spot pass, and synthesis when required. Reviewers receive raw evidence and the predeclared
policy, not the proposed letter. Any sustained concern triggers a tested fix and mechanical
reverification; reviewer votes and prose never award or override a category.

**Commit:** `docs: record independent A-grade trial`

### Task 12: Release once, self-apply, verify, integrate, and clean

Only after Tasks 10 and 11 pass:

1. Put the `0.5.0` version decision on trial, then update Claude manifest, Codex manifest,
   marketplace metadata, README current-source claims, and report pointers together.
2. Run the complete repository matrix on Python 3.10 and the local default, hook/action selftests,
   oracle validation, public-evidence verification, exact `grade --json`, and `git diff --check`.
3. Commit release identity separately.
4. Install the exact committed source to Claude and Codex; run all three doctor modes, fresh host
   discovery, and uninstall dry-runs; regenerate the host-bound grade evidence if the design binds
   machine-local installation separately from the portable public package.
5. Push the feature branch, open/review/merge through the authorized repository workflow, and
   verify the exact merged SHA exists on the remote default branch.
6. Reinstall from the merged source if its SHA differs, re-run doctor and the grade receipt, then
   remove only stale Evergreen-owned caches/links named by dry-run evidence.
7. Confirm both working trees are clean. External marketplace publication remains `unverified`
   unless it was directly performed and checked with authority.

Final verification commands include:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/hooks.sh
bash tests/action.sh
python3 eval/bench/run_bench.py --selftest
python3 -m eval.oracle.build validate --manifest eval/oracle/sources/manifest.json
./bin/evergreen grade verify --repo . --manifest eval/grade/public/0.5.0/evidence.json --json
./bin/evergreen doctor --host claude --repo .
./bin/evergreen doctor --host codex --repo .
./bin/evergreen doctor --host all --repo .
git diff --check
```

**Commit:** `release: prepare Evergreen 0.5.0`

## Completion evidence

- [ ] Eight exact categories recompute to A from raw, committed evidence at one source commit.
- [ ] The five-language holdout satisfies all row, repository, class, coverage, and metric gates.
- [ ] The holdout was unavailable during tuning and executed once for the frozen candidate.
- [ ] Every declared applicable peer ran on exactly the same ID set; unsupported cells are explicit.
- [ ] No superiority or best-in-class claim appears without a separate sustained trial.
- [ ] Trust/security reviews contain no unresolved critical or important finding.
- [ ] Active Claude and Codex installations match the exact merged source and pass doctor.
- [ ] Documentation and all version surfaces agree with the evidence and release identity.
- [ ] Offline public verification passes on macOS and Linux CI.
- [ ] Source, integration, and user configuration cleanup removes only proven Evergreen-owned stale
      artifacts; final receipts are clean.
- [ ] Push and merge claims are bound to authoritative remote evidence for the exact SHA.
- [ ] Adoption and external publication remain explicitly unverified unless directly evidenced.
