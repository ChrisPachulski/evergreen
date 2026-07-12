# Evergreen best-in-class hybrid design

**Status:** Implemented in the 0.4.0 release line; final verification and benchmark publication
pending

**Date:** 2026-07-11

**Baseline:** `b9cfbc1` (`0.3.2`)

## Outcome

Evergreen remains a local, inspectable documentation-truth skill. It gains a thin deterministic
trust layer and can consume optional evidence produced by other tools. Deterministic evidence
narrows and grounds the work; Evergreen remains the final semantic judge.

The revised promise is:

> A local documentation-truth skill that proves drift from repository evidence, accepts optional
> deterministic facts, and reports clean, drifted, and inconclusive runs without confusing them.

This deliberately replaces the absolute “a skill, not a scanner” boundary. Evergreen will not
become a hosted documentation platform or ship a multi-language parser suite.

## Goals

1. Make a false clean certification structurally impossible in CI.
2. Give the semantic reviewer the exact change evidence it is asked to inspect.
3. Treat pull-request content as untrusted data and verify findings before publishing them.
4. Make evaluation results reproducible for the judge that actually ships.
5. Let deterministic tools and source-to-doc maps improve recall without becoming truth verdicts.
6. Provide comparable Claude and Codex installation, diagnostics, and behavior.
7. Bound executable verification so inability to run safely becomes inconclusive, never drift.
8. Generalize release-identity checking to packages and deployed documentation surfaces.

## Non-goals

- No Evergreen-operated hosted index, embeddings store, dashboard, Slack integration, or SaaS.
- No bundled TypeScript, Python, Go, Rust, Java, or Swift AST engine.
- No checksum-only drift verdicts. A source change is an impact signal, not proof of a false claim.
- No truth score that mixes coverage, readability, style, or documentation volume with accuracy.
- No automatic semantic-prose rewrite in the truth path. `flourish` remains explicit.
- No automatic execution of commands declared by an untrusted pull request.
- No parity work based only on an unreleased competitor’s waitlist claims.

## Architecture

The design has four isolated layers.

### 1. Deterministic run preparation

A standard-library helper produces a bounded change manifest before the model runs. The manifest
contains:

- schema version;
- base and head commit IDs;
- changed paths and Git statuses;
- bounded diff hunks;
- symbol and contract seeds mechanically extracted from changed lines;
- truncation indicators and deterministic errors.

Implementation note (`ce27ea3`): the Action passes this manifest plus bounded, term-matched
documentation excerpts from regular blobs at the audited Git head as delimited **untrusted
evidence blocks**. The CI reviewer has no file or shell tools; source evidence comes from manifest
hunks and documentation evidence comes from exact-head context. Any context bound, invalid blob,
or deterministic read failure makes the audit inconclusive.

Repository documentation, source comments, filenames, and provider evidence are always data. They
cannot change the audit instructions, output schema, file scope, or publication policy.

### 2. Semantic review

The existing freshness ladder and prove-it-or-drop-it rules remain canonical. Candidate discovery
uses the union of:

1. paths and contract seeds from the change manifest;
2. normal repository grep/read discovery;
3. optional source-to-doc impact mappings;
4. optional deterministic provider evidence supplied by the trusted caller.

Provider evidence may establish mechanical facts such as “export removed,” “path missing,” or
“signature changed.” It may prioritize candidates and prove the mechanical half of a finding. It
may not certify semantic prose, declare a source change to be drift, or bypass Evergreen’s
exemptions and citation rules.

### 3. Validated result protocol

Every CI review ends with exactly one versioned result envelope. The initial protocol is:

```json
{
  "schema_version": 1,
  "status": "complete",
  "base": "<commit>",
  "head": "<commit>",
  "claims": {
    "total": 12,
    "certified": 9,
    "drift": 2,
    "unverified": 1
  },
  "findings": [],
  "unverified": [],
  "errors": [],
  "runtime": {
    "provider": "anthropic",
    "model": "<resolved model>",
    "cli_version": "<resolved CLI>"
  }
}
```

`status` is `complete` or `inconclusive`. A complete result must satisfy:

`total == certified + drift + unverified`

It must also match the requested base and head commits, contain valid enumerated values, and pass
path/line validation for every finding. A clean certification is permitted only when the envelope
is valid, complete, has zero drift, and has zero unverified claims. Empty output, prose-only output,
truncation, malformed JSON, count mismatch, citation mismatch, timeout, refusal, or model failure
is inconclusive.

Each finding has required `severity`, `category`, `doc_path`, `doc_line`, `claim`, `code_path`,
`code_line`, `why`, and `fix_or_flag` fields. Each unverified claim has required `doc_path`,
`doc_line`, `claim`, and `reason` fields. Paths must be normalized repository-relative paths;
absolute paths, parent traversal, symlink escapes, invalid line numbers, unknown enumerations, and
fields over the protocol limits invalidate the envelope. Before rendering, the validator opens
each cited file at `head`, confirms both line locations exist, and confirms the quoted documentation
claim occurs at the cited documentation location. Code citations prove location and availability;
the semantic reviewer remains responsible for the meaning of the cited code.

The PR Action never fails because documentation drift was found. By default it does fail when the
audit itself is inconclusive, because a green check must mean the requested check ran. A
`fail_on_inconclusive` input allows repositories to choose advisory-only infrastructure behavior,
but the PR comment and step summary still say **inconclusive**, never **clean**.

### 4. Presentation and host integration

The renderer consumes only validated protocol objects. It publishes:

- run status;
- audited base and head;
- certified, drift, and unverified counts;
- findings with verified code and documentation citations;
- explicit infrastructure or scope errors;
- model and CLI identity.

Markdown fields are length-bounded and escaped. The renderer never searches arbitrary model prose
for JSON-like lines.

Claude hooks retain their ride-along behavior. Codex receives the same canonical rules and command
semantics through its plugin and `AGENTS.md` surfaces. A repository-owned Python 3 standard-library
CLI at `bin/evergreen` provides install, doctor, dry-run, and uninstall operations. It must detect
supported hosts, report exact installed versions, preserve existing user configuration, use marked
blocks or links it can remove safely, and smoke-test without modifying a project. It never installs
provider dependencies or edits an existing unmarked instruction block.

## Optional evidence providers

Version 1 is intentionally passive: Evergreen accepts a provider JSON file from a trusted caller;
it does not execute commands named by repository configuration.

Each evidence item contains:

- provider name and version;
- evidence type;
- source path and line/span;
- symbol or contract identity where applicable;
- old and current mechanical facts;
- confidence limited to `deterministic` or `advisory`;
- optional provider-native metadata.

Malformed provider input is ignored with an explicit warning. A provider cannot emit Evergreen
findings directly. Initial documentation and fixtures will demonstrate interoperability with a
generic provider and Drift-shaped facts without making Drift a dependency.

## Source-to-document impact maps

An optional versioned `.evergreen-map.json` associates source globs with living documentation:

```json
{
  "version": 1,
  "maps": [
    {
      "sources": ["src/public-api/**"],
      "docs": ["docs/api.md", "README.md"]
    }
  ]
}
```

Maps expand and rank the candidate set. They never suppress grep-based discovery, prove drift,
certify freshness, or block a change. Invalid patterns are reported and skipped.

`evergreen impact <paths...>` exposes this pre-edit candidate discovery. Its output says which
claims deserve review and why; it never says they are wrong before the proof pass.

## Safe prove-by-test policy

Executable verification remains local and explicit. CI semantic review does not execute code from
the pull request.

When prove-by-test is requested locally:

1. Prefer a repository-declared existing test command over invented execution.
2. Use a bounded timeout and a scratch location that is removed afterward.
3. Do not forward secrets beyond what the selected test command already receives.
4. Disable network access when the available host can do so without adding a runtime dependency.
5. Refuse privileged, destructive, deployment, portal, or publication commands.
6. Report unavailable isolation, setup failure, compilation failure, or timeout as inconclusive.
7. Treat a failing test as drift only when the test faithfully encodes the documented claim and
   the failure comes from running application behavior rather than test setup.

Container or VM orchestration is deferred. Evergreen documents the boundary instead of pretending
portable sandboxing exists where it does not.

## Evaluation integrity

Published metrics must be generated from committed artifacts tied to:

- skill commit;
- judge implementation commit;
- dataset and validated-label hashes;
- model and CLI identifiers;
- protocol version;
- timeout and concurrency settings;
- per-item completion status;
- token/cost and latency summaries where the provider exposes them.

Model timeout, refusal, parse failure, and missing stage output become `abstain/error`. They are not
scored as consistent. Reports include completion coverage beside the confusion matrix and may not
be published below a declared completion threshold.

Before category-leadership language is allowed, the current trial judge must be rerun on Python,
Java, TypeScript, Rust, and Go. Each language gets its own matrix; no aggregate may hide a weak
language. README numbers are generated from the committed result artifact instead of copied by
hand.

## Release identity beyond apps

Release identity becomes a repository-declared relationship among local sources of truth and
public surfaces, including:

- package manifests and registry versions;
- CLI `--version` output;
- documentation badges and installed-command examples;
- generated API documentation;
- deployed documentation site labels;
- existing app marketing/build versions and related targets.

External state is checked only when it can be queried directly and is explicitly linked to the
same product. Otherwise the result is `external state unverified`. Independent version streams are
allowed when the repository documents the policy.

## Delivery sequence

The work is intentionally split into independently releasable waves.

### Wave 1: trustworthy CI core

- change manifest;
- hostile-input boundary;
- versioned result envelope and validator;
- citation verification;
- explicit inconclusive behavior;
- pinned Claude CLI;
- adversarial end-to-end Action tests;
- close the compound `git add && git commit` hygiene-guard bypass found during review.

No later wave ships before Wave 1 passes.

### Wave 2: trustworthy evidence

- evaluator abstentions and completion coverage;
- current-judge reproducible reruns;
- generated metric documentation;
- Python, Java, TypeScript, Rust, and Go matrices.

### Wave 3: hybrid discovery

- passive provider-evidence schema;
- `.evergreen-map.json`;
- `evergreen impact`;
- fixtures proving provider facts never become semantic verdicts automatically.

### Wave 4: portable product surface

- Claude/Codex installer, doctor, dry-run, and uninstall;
- consistent command documentation and smoke tests;
- generalized release-identity rules and fixtures;
- documented safe prove-by-test contract.

Each wave receives its own implementation plan and may be released separately. The expected first
feature release is a minor pre-1.0 increment, but the actual marketing version is chosen only after
the completed diff is audited against the release policy.

## Testing strategy

### Unit tests

- change-manifest bounds, path handling, statuses, truncation, and unusual filenames;
- protocol schema, invariants, enumerations, limits, and error classification;
- citation verification for missing paths, invalid lines, moved files, and mismatched excerpts;
- renderer escaping and field-size limits;
- map and provider schema validation;
- benchmark abstention and coverage calculations.

### Integration tests

Run the full PR driver against temporary Git repositories with stubbed `claude` and `gh`
executables. Cover:

- explicit clean completion;
- findings and unverified claims;
- malformed, truncated, empty, and prose-only responses;
- wrong base/head and count mismatches;
- hostile documentation instructions;
- attempts to cite files outside the scoped checkout;
- missing CLI, missing credentials, timeout, and comment-upsert failure;
- fork pull requests without secrets;
- advisory and failing inconclusive modes;
- compound and option-prefixed Git commands through the hygiene guard.

### Regression fixtures

- deterministic provider fact plus semantic false positive that Evergreen must reject;
- source change acknowledged by checksum while the documentation remains wrong;
- mapped and unmapped documentation candidates in the same change;
- executable claim whose test cannot run safely;
- independently versioned package and documentation surfaces;
- cross-language benchmark completion and abstention cases.

## Acceptance criteria

The program is complete when:

1. No model or infrastructure failure can render a clean certification.
2. The PR reviewer receives a validated representation of the requested diff.
3. Every published finding has a locally verified in-repository path and valid line reference.
4. Hostile repository prose cannot alter the protocol or cause arbitrary model prose to be posted.
5. Drift remains advisory while inconclusive execution is unmistakable and policy-controlled.
6. The current judge’s published metrics are reproducible from matching committed artifacts.
7. Provider evidence and mappings improve candidate discovery without becoming verdicts.
8. Claude and Codex installation/status paths are both documented and smoke-tested.
9. Prove-by-test has an explicit safety boundary and safe failure semantics.
10. Release identity covers apps, packages, and linked public documentation surfaces without
    conflating independent version streams.

## Rejected alternatives

### Preserve a pure prompt-only architecture

Rejected because the current CI cannot reliably distinguish clean output from malformed output,
cannot see the diff through its allowed tools, and repeatedly asks a model to rediscover facts
deterministic tools already know.

### Rebuild Drift or another language parser suite

Rejected because it sacrifices Evergreen’s language-agnostic reach, duplicates a large maintenance
surface, and makes installation materially heavier. Provider interoperability captures the useful
facts without owning every parser.

### Build a hosted documentation platform

Rejected because Moxie Docs and Mintlify already occupy indexing, search, generated-doc, MCP, and
collaboration workflows. Evergreen’s defensible identity is a local, auditable, high-precision
truth layer with no Evergreen-operated repository store.
