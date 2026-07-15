# Measurement-First Executable-Oracle Salvage Plan

> **For agentic workers:** This replaces the unfinished A-certification plan. Do not reconstruct
> the removed v2 custody, ledger, attestation, OCI/seccomp, or grade framework from historical
> context. Do not execute tests, corpus code, or benchmarks until the static freeze in Task 2 is
> reviewed and the user separately authorizes execution.

**Goal:** Preserve the authentic executable-oracle experiment, establish an immutable development
measurement, and let observed detector behavior determine the next engineering work.

**Architecture:** Keep the standard-library Python pilot and existing pinned source catalogs. Use
the repository's existing benchmark, grade, receipt, and publication code if measurement later
shows that more machinery is needed. Add no external attestation, container, sandbox, policy, or
grade system before a concrete measured requirement exists.

**Current design boundary:** `docs/DESIGN.md#measurement-first-quality-and-release-boundary`

**Historical design context:** `docs/superpowers/specs/2026-07-14-a-grade-evidence-design.md`

## Global constraints

- Current execution remains prohibited until the exact inputs and evaluator are frozen and
  statically reviewed.
- The pilot executes repository Python as the current user; only explicitly trusted source
  repositories may be selected without a real security sandbox.
- Development evidence is never called holdout evidence.
- No stored grade, pass flag, or post-hoc threshold may turn a measurement into an A.
- No A, best-in-class, release, publication, or external-adoption claim is in scope.
- The existing immutable `0.4.0` benchmark identity and metrics are not renamed or inherited.
- No dependency, provider call, installation, push, publication, or release mutation is added.

## Task 1: Salvage the experiment

- [x] Keep the pinned Python, Java, TypeScript, Rust, and Go source catalogs already committed.
- [x] Move the Python pilot out of the abandoned `v2` package and remove its dependency on v2
      control code.
- [x] Retain the pilot's focused integration test without executing it during static salvage.
- [x] Remove the unfinished v2 custody, split, runtime, ledger, attestation, category, and grade
      implementation from the active tree.
- [x] Mark the old A-certification design as superseded and non-authorizing.

## Task 2: Freeze a development measurement before execution

- [ ] Select a small, reviewable set of authentic Python source/test pairs from pinned trusted
      repositories.
- [ ] Expand the pilot only for mutation patterns justified by those exact pairs; do not add a
      generic operator abstraction in advance.
- [ ] Record exact candidate commit/tree, source commit/tree, source and test paths, assertion byte
      span, mutation implementation hash, evaluator hash, command argv, environment allowlist,
      timeout/output bounds, and result path.
- [ ] Record a content-addressed input inventory and require exact equality at execution time.
- [ ] Statically review the complete frozen package for ambiguity, mutable references, hidden
      dependencies, unsafe repository code, and data leakage.
- [ ] Obtain explicit authorization before running any test or measurement.

## Task 3: Measure, then decide

- [ ] Run the smallest authorized pilot checks.
- [ ] Run the frozen development measurement once against the frozen candidate.
- [ ] Publish raw counts and limitations without a letter grade.
- [ ] Choose the next action from the evidence: improve the detector, revise the corpus, stop the
      approach, or design the smallest integrity control required by an observed failure.
- [ ] Declare any future threshold before the run it will grade.

## Deferred work

Five-language scale, a secret holdout, same-corpus peer comparison, external attestation, OCI
sandboxes, lifecycle ledgers, and A-grade certification are deliberately deferred. They return only
when measurement shows they answer a real question and their required authority actually exists.
