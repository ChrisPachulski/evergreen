# Evidence-Backed Completion Receipts

**Date:** 2026-07-13

## Goal

Make project identity, source-shipping state, benchmark identity, and external-release boundaries
explicit and reproducible for both Claude and Codex. An agent must not report that work was pushed,
merged, cleaned, released, lost, complete, or not run from conversational memory alone.

## Problem

Evergreen already protects documentation claims and blocks many unsafe Git command shapes, but it
does not give an agent one deterministic receipt for answering operational status questions. That
gap allowed three distinct states to be conflated:

- source integrated and pushed to a repository branch;
- a frozen provider-backed benchmark previously executed and reverified offline;
- a separate future human-held-out benchmark not yet executed.

The same gap allowed the current working directory to be mistaken for the target of an earlier
mutation and allowed an empty prune operation to be described as cleanup performed.

## Success criteria

1. `evergreen receipt` produces a bounded, deterministic, read-only repository receipt.
2. The receipt identifies repository root, origin, branch or detached state, HEAD, upstream,
   ahead/behind counts, and staged, unstaged, and untracked state.
3. The receipt distinguishes local source state from external release state. It never infers a
   registry, marketplace, deployment, store, or GitHub Release from local Git evidence.
4. An optional Evergreen public benchmark manifest adds a fully named benchmark identity without
   turning a manifest declaration into a provider-execution or quality claim.
5. Claude and Codex receive one synchronized instruction contract requiring fresh receipts for
   absolute operational claims and prohibiting unsupported polarity reversals.
6. The existing rule requiring separate staging and commit calls remains explicit and gains a
   receipt requirement before a completion claim.
7. Tests prove exact output semantics, unsafe-input refusal, host-instruction alignment, and that
   no network or repository mutation is performed.

## Non-goals

- Evergreen will not commit, merge, push, tag, publish, deploy, or poll CI.
- Evergreen will not query GitHub, registries, marketplaces, stores, or deployment providers.
- Evergreen will not claim that benchmark coverage PASS means detector-quality PASS.
- Evergreen will not create a persistent transaction database or write receipt files.
- Evergreen will not invent a generic benchmark format for unrelated projects.

## Command interface

```text
evergreen receipt [--repo PATH] [--benchmark-manifest PATH] [--json]
```

- `--repo` defaults to `.` and may point anywhere inside a Git working tree.
- `--benchmark-manifest` is optional. It accepts only an in-repository regular file using the
  checked-in Evergreen public benchmark-publication manifest schema.
- `--json` emits one compact JSON object with sorted keys. Human output renders the same fields and
  states without adding interpretation.
- The command is read-only and network-free.

Exit status:

- `0`: a complete receipt was produced;
- `2`: the path is not a repository or supplied evidence is invalid, unsafe, or incomplete;
- `1`: a bounded local operation failed unexpectedly.

## Repository receipt schema

JSON output has this shape:

```json
{
  "benchmark": null,
  "release": {
    "external_state": "unverified",
    "local_tags": []
  },
  "repository": {
    "ahead": 0,
    "behind": 0,
    "branch": "main",
    "clean": true,
    "detached": false,
    "head": "0123456789abcdef0123456789abcdef01234567",
    "name": "example",
    "origin": "https://example.invalid/owner/example.git",
    "root": "/absolute/path/example",
    "staged": 0,
    "unstaged": 0,
    "untracked": 0,
    "upstream": "origin/main"
  },
  "schema_version": 1
}
```

Rules:

- Missing origin and upstream are represented as `null`, not errors.
- Detached HEAD uses `branch: null`, `detached: true`, and `upstream: null`.
- `ahead` and `behind` are `null` when no upstream exists.
- `clean` is true only when staged, unstaged, and untracked counts are all zero.
- Ignored files do not make the repository dirty.
- `local_tags` contains only tags pointing at HEAD, sorted bytewise.
- `external_state` is always `unverified`; local tags never prove external publication.
- No timestamps are emitted, so unchanged state produces byte-identical JSON.

## Benchmark identity

When `--benchmark-manifest` is present, `benchmark` contains:

```json
{
  "artifact_count": 5,
  "evaluated_release": "0.4.0",
  "evidence_state": "declared_publication",
  "languages": ["Java", "Python", "go", "rust", "typescript"],
  "manifest": "eval/bench/public/0.4.0/manifest.json",
  "provenance_commit": "0123456789abcdef0123456789abcdef01234567",
  "provider": "codex",
  "report": "eval/bench/results-0.4.0.md"
}
```

The loader must require schema version `1`, kind
`evergreen-benchmark-decision-publication`, a non-empty evaluated release and provider, a full Git
commit ID, unique declared languages, an artifact count matching the language set, and normalized
repository-relative manifest, artifact, dataset, and report paths. It refuses absolute paths,
traversal, symlinks, non-regular files, malformed UTF-8/JSON, duplicate languages, oversized
manifests, and evidence outside the repository.

`evidence_state` deliberately says `declared_publication`. The receipt names the publication but
does not claim that provider calls were freshly executed, that artifact hashes were reverified, or
that detector quality passed. Those stronger claims require the repository-declared verifier and
its fresh output. This prevents a status command from overstating what parsing a manifest proves.

## Claude and Codex instruction contract

The canonical `AGENTS.md`, full skill, and digest must share these exact operational rules:

1. Before an external mutation, lock the target using repository root, origin, branch, pre-mutation
   HEAD, and intended operation. A continuation such as “ship” remains bound to that target.
2. Before reporting pushed, merged, clean, complete, released, lost, erased, or not run, obtain
   fresh evidence. For repository state, run `evergreen receipt --repo PATH`; for benchmarks, name
   evaluated release, resolver/judge, provider, languages, provenance commit, and whether the
   evidence was executed, reverified, published, or merely planned.
3. Never reverse an earlier project, mutation, benchmark, or release-status claim without new
   evidence. State the prior claim and the evidence that changes it.
4. Treat “pushed to source branch,” “tagged,” “GitHub Release published,” “marketplace published,”
   and “deployed” as separate states.
5. Empty cleanup output means nothing was removed.
6. Stage and commit in separate tool calls. A combined shell call cannot prove the finalized staged
   index passed the guard.
7. When a user challenges remembered status, inspect the receipt or authoritative artifact before
   agreeing or defending.

The installed Claude and Codex surfaces must inherit the same contract from canonical files. Hook
tests fail if any exact shared sentence drifts.

## Implementation boundaries

- Add a focused `evergreen/receipt.py` module for bounded Git reads and manifest identity parsing.
- Extend `bin/evergreen` with the `receipt` subcommand and rendering only; keep business logic out
  of the entry point.
- Reuse standard-library `subprocess`, `json`, `pathlib`, and `urllib.parse`; add no dependency.
- Invoke Git with argv arrays, `--no-replace-objects`, bounded timeouts, bounded decoded output, and
  no shell.
- Do not print environment variables, Git configuration, credential helpers, or remote credentials.
  HTTP(S) remote userinfo must be redacted; unsupported credential-bearing remote forms are shown
  only in a bounded redacted representation.
- The command never writes Git state or repository files.

## Test design

Unit tests must cover:

- clean synchronized branch with origin and upstream;
- staged, unstaged, untracked, and combined dirty counts;
- ignored files excluded from dirty state;
- detached HEAD, missing upstream, and missing origin;
- ahead and behind counts;
- tags pointing at HEAD only;
- deterministic compact JSON and equivalent human output;
- non-repository and missing-path errors;
- bounded Git failure and output limits;
- credential redaction;
- valid five-language benchmark identity;
- malformed, oversized, outside-root, traversal, absolute, symlink, duplicate-language, wrong-kind,
  wrong-schema, mismatched-artifact-count, and incomplete benchmark manifests;
- proof that receipt generation leaves HEAD, index, worktree, refs, and filesystem unchanged.

Integration tests must assert:

- `evergreen receipt --help` documents every option;
- Claude, Codex, product, full-skill, and digest surfaces share the operational contract;
- documented commands execute exactly;
- the complete Python, hook, Action, benchmark-self-test, and public-verification gates remain green.

## Completion and grading gate

The work is complete only when:

- every focused test first failed for the missing behavior and then passed;
- all existing tests and integrations pass locally;
- an independent reviewer finds no critical, important, or minor defect;
- Ubuntu Python 3.10 compatibility, Ubuntu Python 3.11, and macOS Python 3.11 CI pass;
- the final report names source integration and external publication separately;
- the worktree is clean and the mutation receipt matches the pushed commit.

These gates support an A only when evidenced. They do not permit an A to be declared from intent,
partial execution, or conversational confidence.
