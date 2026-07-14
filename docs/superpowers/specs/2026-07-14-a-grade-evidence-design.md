# Evidence-Earned A Grades

**Date:** 2026-07-14

## Goal

Make every internally controllable Evergreen grade reproducible from checked-in policy and bounded
evidence. A grade is an output of a verifier, never prose assigned by the implementer. The verifier
must refuse missing, stale, circular, selectively omitted, or self-asserted evidence.

Externally dependent adoption, publication, marketplace state, and human identity remain
`unverified` unless directly evidenced. They never become an internal A-grade blocker and never
silently become an A.

## Correction to the earlier benchmark design

Independent human review is useful external-validity evidence, but software cannot prove that an
annotator is human or independent. Therefore human annotations are supplementary and non-gating.
The internally reproducible detector gate uses mechanically adjudicated claims whose truth can be
rerun from an executable or structural oracle.

This does not prove universal natural-language understanding. It proves quality only for the named
claim classes, languages, repositories, prevalence, candidate, and peer set in the evidence
manifest. Evergreen must keep that boundary visible in every grade and release claim.

## Approaches considered

1. **Human gold labels:** high face validity, but human identity and independence are not
   machine-verifiable. Retain only as supplementary evidence.
2. **Multi-model consensus:** reproducible but circular for a model-judged detector. Retain only as
   exploratory evidence.
3. **Executable-oracle benchmark:** deterministic, offline-reverifiable ground truth with explicit
   construct limits. Use this for the internal detector grade.

## Grade contract

The checked-in policy defines categories, evidence inputs, thresholds, and failure precedence. The
grade verifier emits `A` only when every required predicate for that category passes. Otherwise it
emits `not-earned` with machine-readable reasons. It does not emit partial letter grades.

The overall internally controllable grade is `A` only when every category below is `A` in one
receipt bound to the same source commit:

| Category | Required evidence |
| --- | --- |
| Detector quality | Untouched five-language oracle holdout clears all absolute quality and coverage gates. |
| Same-corpus comparison | Every declared runnable peer is scored from the identical holdout IDs and labels; omissions fail closed. |
| Trust and security | Complete repository security/trust matrix passes on the bound commit with no unresolved critical or important review finding. |
| Claude self-application | Active Claude installation hashes and ownership record match the bound release source; doctor passes. |
| Codex self-application | Active Codex installation hashes and ownership record match the bound release source; doctor passes. |
| Documentation and release honesty | Canonical version surfaces agree; benchmark and external-state claims match their receipts; freshness checks pass. |
| Reproducibility and CI | Offline re-verification succeeds on macOS and Linux CI for the same committed evidence package. |
| Cleanup | Bound source tree is clean and contains no forbidden generated, private-label, credential, machine-local, or stale-version artifacts. |

Adoption and external publication are separate states rendered as `unverified`, `verified`, or
`not-applicable`; they are never letter-graded by the internal verifier.

## Detector oracle corpus

### Supported claim classes

Version 1 covers direct facts that an oracle can settle without semantic opinion:

- return values and output values;
- parameter names, accepted values, and defaults;
- raised errors and documented error conditions;
- constants, bounds, and collection cardinality;
- observable state changes and side effects in a disposable harness;
- explicitly documented invariants that a parser, compiler, or test can prove.

Architecture rationale, tutorials, quality adjectives, implied intent, and behavior requiring unseen
external systems are outside this corpus. The detector may still review them in normal use, but the
oracle grade does not claim coverage of them.

### Source and mutation rules

- Cover Python, Java, TypeScript, Rust, and Go.
- Derive examples from hash-pinned, license-compatible public repositories or checked-in benchmark
  sources with recorded origin, commit, license, and extraction recipe.
- Keep whole repositories in one split. A source claim and every derivative remain in one split.
- A consistent row must pass its declared oracle. An inconsistent row is created by one recorded
  mutation and must fail the same oracle for the intended factual reason while continuing to parse
  or compile as required by that mutation class.
- Mutation operators are finite and versioned. The corpus generator cannot accept a free-form label.
- Every row records source hash, mutation ID or `none`, oracle kind, harness hash, expected outcome,
  observed outcome, and project identity.
- Corpus validation is offline, bounded, network-free, and all-or-nothing. One unresolved oracle
  invalidates the package.

### Split and freeze

- Select development and holdout membership by keyed repository-group hashing before detector work.
- Commit only the ID-only, hash-bound split manifest. Keep the holdout rows unavailable to detector
  development.
- Require at least 100 positive and 100 negative holdout decisions per language, drawn from at
  least 10 source repositories per language, with no repository contributing more than 20% of one
  language's holdout.
- Freeze source commit, skill/prompt digest, resolver, provider, model, runtime versions, oracle
  package, split manifest, peer set, and all thresholds before holdout access.
- Run the holdout once. A failure starts a new versioned split; row-level holdout reasoning may not
  be used to tune the failed candidate.

### A thresholds

For every language independently:

- provider completion `>= 0.99`;
- semantic decision coverage `>= 0.99`;
- precision `>= 0.80`;
- recall `>= 0.80`;
- F1 `>= 0.80`;
- specificity `>= 0.90`;
- no unreported or dropped row;
- two-sided 95% confidence intervals and raw confusion counts are published.

Report balanced metrics and prevalence-adjusted metrics at 10% drift. The grade uses the 10%
prevalence result. Thresholds are fixed before the candidate sees holdout rows.

## Same-corpus peer comparison

The peer manifest names every runnable peer selected before the holdout, its exact source/version,
configuration, runtime, adapter, and applicability by language. At minimum it includes:

- the same base model with a single-pass direct classification prompt, measuring Evergreen's added
  trial value;
- every open, locally runnable documentation-drift peer that can consume the claim classes without
  changing the labels or seeing Evergreen outcomes.

Each applicable peer receives the identical code, documentation, IDs, and oracle labels. Unsupported
languages are explicit `not-applicable`, never zeroes and never silent omissions. Comparison quality
earns A when the declared peer matrix is complete, reproducible, and same-corpus. It does not require
Evergreen to win and cannot support `best in class` by itself. Any superiority statement requires a
predeclared effect-size test and confidence interval that excludes parity.

## Evidence package and verifier

Use one versioned, standard-library JSON policy and one evidence manifest. Reuse the existing
bounded-path, exact-HEAD, deterministic-serialization, public-manifest, receipt, and report helpers
instead of introducing a second trust framework.

The evidence manifest binds:

- schema/kind and evaluated release;
- exact source commit and tree;
- policy, split, dataset, oracle, resolver, prompt, peer-manifest, result, report, and CI-run hashes;
- per-category evidence states and commands;
- detector raw counts and thresholds by language;
- peer applicability and exact ID-set hashes;
- host installation roots, ownership records, canonical-file hashes, and doctor results;
- external states without converting them to grades.

The verifier must:

1. load only bounded regular files beneath the repository without following symlinks;
2. require exact captured-HEAD bytes for public evidence;
3. reject duplicate keys, unknown required-category names, non-finite numbers, threshold overrides,
   partial language sets, stale commits, dirty inputs, and selectively missing peer rows;
4. recompute grades from raw evidence rather than trust stored letters or pass booleans;
5. emit deterministic JSON and human output with every failure reason;
6. perform no network call, provider call, installation, publication, or Git mutation.

## Self-application

Source-only support is not self-application. Claude and Codex each earn A only when:

- the canonical release source, active installed skill/commands, host manifest, and ownership record
  have identical declared version and content hashes;
- installation safely migrates a stale Evergreen-owned link or tree but refuses paths without
  ownership proof and refuses to overwrite unrelated user content;
- `evergreen doctor --host claude`, `--host codex`, and `--host all` pass after installation;
- a fresh host discovery check loads the current release rather than an older plugin cache or sync
  link;
- uninstall dry-run names only Evergreen-owned paths.

Symlinked host roots are supported only when their fully resolved destination is user-owned,
non-world-writable, and covered by an explicit ownership record. A symlink alone is neither proof
of ownership nor a reason to reject a legitimate managed configuration.

## Release and claim policy

- Keep `0.4.0` attached to its frozen historical benchmark.
- The integrated feature set is eligible for `0.5.0` only after every internal category passes.
- A failed detector holdout does not become the current result and does not trigger a release bump.
- README and report language must name the measured claim classes and say that broader external
  validity, adoption, marketplace publication, and human review remain unverified when they are.
- `best in class` remains prohibited unless a future same-corpus superiority protocol actually
  earns that separate claim.

## Delivery sequence

1. Commit this grade contract and an executable implementation plan before detector tuning.
2. Implement the grade policy/verifier and its anti-gaming tests.
3. Implement and validate the five-language oracle corpus generator and split/freeze machinery.
4. Repair ownership-aware Claude/Codex self-application with test-first migrations.
5. Freeze the candidate and peer manifest, then run the development set.
6. Tune only on development evidence until its thresholds pass.
7. Freeze a clean candidate, run the untouched holdout and peers once, and generate the grade receipt.
8. Run independent security, specification, and verdict-on-trial reviews for every proposed A.
9. Only after all internal categories pass: update release identity once, install both hosts, run CI,
   merge, push with exact remote evidence, and clean obsolete Evergreen-owned installations.

## Non-goals

- Proving that an annotator is human or independent.
- Claiming universal semantic coverage from mechanically decidable claim classes.
- Manufacturing adoption, publication, marketplace state, or social proof.
- Assigning an A from implementation intent, test count, a narrow passing fixture, or a reviewer opinion.
- Claiming best-in-class performance without a separately sustained superiority verdict.
