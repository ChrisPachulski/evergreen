# Evergreen — operations reference

Everything the [README](../README.md) demotes: release-identity rules, the receipt policy, the
evidence boundary, host-install transaction semantics, and the full CI contract. Nothing here is
summarized — it is the same text, moved so the README can stay a gateway.

The one rule all of it serves: **prove it or drop it.** If evergreen can't cite the code that makes
a doc wrong, it isn't a finding.

## Release identity

Release identity spans package manifests, registry versions, and version-reporting CLI output.
Audit version-bearing badges, version-reporting installed-command examples, generated API version labels or headers, and deployed docs version labels as linked release claims.
Interpret each claim's meaning: current source and latest published release may legitimately differ.
Keep independently versioned packages and platforms as independent release streams unless repository policy explicitly couples them.
Without direct registry, store, or deployment evidence, report external release state unverified.
Never publish, upload, push, deploy, or mutate a portal or registry without explicit user authority.

For Apple apps, the existing rules remain: audit product milestones since the marketing version
last changed, advance the binary build monotonically, and verify related app/extension targets
resolve the same release identity. See the [package mismatch example](../examples/package-release-identity.md)
for a non-app stream that distinguishes an unreleased source version from the latest public release
while leaving registry and deployment state deliberately unverified.

## Evidence-backed completion receipts

<!-- evergreen-receipt-policy:start -->
Before an external mutation, lock the target repository root, origin, branch, pre-mutation HEAD, and intended operation.
A continuation such as “ship” remains bound to that target.

Before reporting pushed, merged, clean, complete, released, lost, erased, or not run, obtain fresh evidence.
Never reverse an earlier project, mutation, benchmark, or release-status claim without new evidence.
State the prior claim and the evidence that changes it.
Treat pushed to a source branch, tagged, GitHub Release published, marketplace published, and deployed as separate states.
Evergreen receipt is a local snapshot only.
An ahead count of zero does not prove the remote branch contains HEAD.
Reporting pushed or merged requires authoritative remote evidence bound to the exact commit SHA.
Absence of a receipt, artifact, or log does not prove that work was not run, lost, or erased; without an authoritative ledger, report the state as unverified.
A benchmark claim names the evaluated release, resolver/judge, provider, languages, provenance commit, and every applicable evidence state.
Benchmark executed, reverified, published, and planned are independent states; report each applicable state and never infer one from another.
Empty cleanup output means nothing was removed.
Stage and commit in separate tool calls.
When a user challenges remembered status, inspect the fresh receipt or authoritative artifact before agreeing or defending.
A combined staging-and-commit call cannot prove the finalized index passed the guard.
Receipt collection is supported on macOS and Linux; unsupported hosts fail before POSIX operations.
Repositories with external clean/process filters, tracked submodules, split indexes, or assume-unchanged/skip-worktree index flags are refused rather than certified.
A benchmark manifest is accepted only when its exact bytes match the captured HEAD.
<!-- evergreen-receipt-policy:end -->

Run `./bin/evergreen receipt --repo .` for a fresh, deterministic view of local repository state.
Local Git state does not verify a GitHub Release, marketplace publication, registry, store, or
deployment; without direct authority, external release state remains unverified.

Human output presents the repository, release, and optional benchmark evidence without adding a
verdict:

```text
Repository receipt:
- root: /path/to/evergreen
- name: evergreen
- origin: https://github.com/ChrisPachulski/evergreen.git
- branch: main
- HEAD: 0123456789abcdef0123456789abcdef01234567
- upstream: origin/main
- ahead/behind: 0/0
- changes: staged=0 unstaged=0 untracked=0
- clean: true
Release evidence:
- local tags at HEAD: none
- external state: unverified
Benchmark evidence:
- none
```

Use `--json` when another tool needs the same fields:

```json
{"benchmark":null,"release":{"external_state":"unverified","local_tags":[]},"repository":{"ahead":0,"behind":0,"branch":"main","clean":true,"detached":false,"head":"0123456789abcdef0123456789abcdef01234567","name":"evergreen","origin":"https://github.com/ChrisPachulski/evergreen.git","root":"/path/to/evergreen","staged":0,"unstaged":0,"untracked":0,"upstream":"origin/main"},"schema_version":1}
```

An optional checked-in public benchmark manifest identifies declared evidence; it does not prove a
fresh provider execution, artifact reverification, or detector-quality result.

`./bin/evergreen grade verify --repo PATH --manifest PATH [--json]` is the mechanical half of that
boundary. It reads a committed manifest at `eval/grade/public/<version>/evidence.json` and derives
the grade itself: the manifest may carry observations only, so a `grade`, `pass`, or `success` key,
a threshold override, or a runtime `evidence_head` is rejected before evaluation. It scores against
`eval/grade-policy-v1.json` exactly as frozen in the subject commit, and the eight required
categories and their gates are pinned in code, so a rewritten policy is refused rather than honored.
It also refuses to grade itself: the verifying checkout must be clean with `bin/evergreen`,
`evergreen/grade.py`, `evergreen/receipt.py`, and `eval/grade-policy-v1.json` matching its own HEAD,
and its commit must appear in the candidate's history strictly before the subject. Read-only; exit 0
only on a derived `A`, 1 when `inconclusive`, 2 otherwise. The verifier ships; an earned grade does
not — this tree publishes no `eval/grade/public/` manifest, and the A-grade certification is not an
active release gate.

The semantic pass may gather optional local evidence with read, grep, diff, or a scratch test. In CI,
the deterministic trust layer does the mechanical work: it binds a bounded change manifest and
matched documentation excerpts to the exact base/head commits, validates counts and citations
against Git at that head, enforces runtime identity, and renders only a valid result envelope. The
CI model has no file or shell tools. Repository files, diffs, paths, excerpts, and comments are
**untrusted data**; instructions embedded in them never change the audit or publication rules.

## Hybrid evidence boundary

Provider evidence and source maps nominate candidates, never findings or verdicts.
Re-read every candidate against current code before deciding drift.

`bin/evergreen impact [--repo PATH] [--evidence FILE] [--json] PATH...` is a read-only candidate
query. It accepts records described by [`evidence-provider-v1.schema.json`](../schemas/evidence-provider-v1.schema.json)
and repository-local source maps, ranks likely documentation, and reports malformed inputs as
warnings. Deterministic confidence means the provider proved its mechanical fact; it does not prove
a documentation claim false. `bin/evergreen till [--repo PATH] [--json] [PATH...]` is its read-only
sibling: a deterministic, ranked inventory of the tracked public declaration surface that seed
consumes, failing closed with a `truncated` warning whenever the inventory it builds could be
incomplete. Tracked files in languages it cannot parse, extensionless scripts without a Python
shebang (a Python-shebang script such as `bin/evergreen` joins the parsed inventory), and untracked
source stay outside that inventory and are named in an `outside inventory` warning instead of
vanishing silently.
Drift-shaped adapters may translate mechanical facts into this
schema, but provider-supplied findings and verdicts are rejected at the boundary. See the
[`provider-evidence.json`](../examples/provider-evidence.json) sample and the
[semantic false-positive example](../examples/provider-boundary.md).

## What `impact` scans, and how it ranks

Without configuration, the command searches bounded, Git-tracked living docs for exact changed
paths and declaration-shaped contract symbols. A symbol counts only where the declaration is
reachable from outside its file: function-local declarations, members of a private type, and
declarations a language marks private (a leading underscore, a lowercase Go initial, a Rust item
without `pub`) are not contracts. It excludes docs inside directory components named
`plans`, `specs`, `adr`/`adrs`, `archive`/`archives`, `audit`/`audits`, `roadmaps`, or `readiness`, plus
changelog and ISO-dated filenames. A repository-local
`.evergreen-map.json`, if present, adds explicit relationships; use the
[`evergreen-map-v1` schema](../schemas/evergreen-map-v1.schema.json) and the shipped
[`evergreen-map.json` example](../examples/evergreen-map.json). Human and `--json` output contain
candidates, reasons, and warnings only—never findings or verdicts—and the query does not write
project state.

A symbol match is ranked by how far the surrounding prose commits to it being code. A quoted,
called, or qualified reference (`` `resolve` ``, `resolve()`, `router.resolve`, a fenced block)
ranks highest; a bare identifier-shaped word ranks lower; a bare plain word — the English verb in
"the path must resolve to a regular file" — ranks lowest. A symbol carried widely across the
scanned corpus is a vocabulary word rather than a link, and is demoted — one tier when it appears
in a fifth of the docs, two when it appears in nearly a third or more. Ranking orders the
candidate set; it never suppresses a candidate, and it never settles the semantic claim.

## Host install — transaction semantics

Use `install --dry-run` or `uninstall --dry-run` to preview. A dry run only reads: when a pending
transaction is on disk it names the artifacts and refuses rather than recovering them, because
recovery is a write. Setup records an owned instruction
block and skill link; uninstall removes only that owned state. It refuses ambiguous, unowned, or
unsafe paths and rolls back ordinary operation failures across the selected hosts. Host mutation
requires exclusive access: preflight and postimage checks refuse detected conflicts, preserve
concurrent state, and report manual recovery instead of claiming a false rollback. Instruction
files and their rollback snapshots are limited to 1 MiB, ownership records to 4 KiB, and each
plugin manifest to 64 KiB; sparse files are checked by logical size. `doctor` makes no configuration
changes or executes plugin code: it validates the canonical command, rules, manifest agreement,
ownership, and links, then performs bounded UTF-8, shebang, and Python AST validation of canonical
`bin/evergreen`.
A typed transaction engine acquires every selected-host lock before recovery or mutation and
journals every create, replace, link, and delete. It automatically resolves only exact bounded
crash states; malformed, conflicting, or unverifiable journals fail closed with bounded manual
recovery paths.
A replaced skill link aborts the entire selected-host uninstall before any instruction, link, or
ownership state is changed.

## What the plugin costs per session

What it costs, since you count tokens: session start injects a compact digest—currently about two-fifths of the full skill by words—not the full ruleset. The [digest](../skills/evergreen/DIGEST.md)
loads at startup, the full skill loads on demand, and the post-turn nudge fires once per new change,
not on every turn while the tree sits dirty.

## CI — the full contract

### What a green check means

Drift never fails the build. Under the default fail-closed policy, a green check means the result
passed protocol validation — commit-bound, shape-checked, citations resolved at head — on any PR
that actually reached review; advisory `fail_on_inconclusive: false` runs can be green while still
reporting an inconclusive audit. The nothing-to-check early exit is also green, but it never
reached review and never produced or validated a result envelope.

### Fork pull requests

Fork PRs get no repository secrets, so `anthropic_api_key` is empty and the Action reports
inconclusive — with `fail_on_inconclusive: true` as shown, that fails every external fork PR
that has code changes alongside tracked docs to check; a fork PR that trips the no-code/no-docs
early exit above passes green regardless, since that check runs before the empty-key check.

(This repo's own `.github/workflows/evergreen-pr.yml` looks different on purpose: dogfooding
means handling untrusted fork PRs safely, so it uses `pull_request_target` with a
trusted/untrusted double checkout. In practice every fork PR is still marked inconclusive
before provider review, secret or no secret — the double checkout buys safety, not fork review.
A consumer repo doesn't share that threat model — the snippet above is the right shape for you,
and `action.yml` accepts both.)

### The four outcomes

The outcomes are explicit:

- **complete and clean** — the validated review finished with no drift and no unverified claims.
- **complete with findings** — proven drift is reported, but the Action still exits successfully.
- **complete with unverified** — the review finished, but it names claims the available code could
  not settle; this is reported and is not a clean certification.
- **inconclusive** — the audit itself could not be trusted or completed, such as malformed output,
  truncated evidence, invalid citations, missing credentials, or a tool failure. This fails by
  default. Set `fail_on_inconclusive: false` for advisory-only infrastructure behavior; the report
  still says inconclusive and never pretends to be clean.

### Process bounds

The pinned provider process runs with project customizations disabled, an allowlisted environment,
no model tools, no session persistence, a 600-second wall-clock ceiling, a 262,144-byte output
ceiling, and a USD 5 default budget. Override the last three with `model_timeout_seconds`,
`max_model_output_bytes`, and `max_budget_usd`. Context generation reads only regular tracked
documentation blobs from the audited Git head; a symlink, invalid blob, deadline, scan/output
limit, or truncated manifest makes the audit inconclusive. Fork PRs without repository secrets are
reported as inconclusive before the provider runs.

On POSIX, timeouts stop the pinned CLI and children that remain in its inherited process group.
Deliberately detached descendants are outside portable standard-library containment and require
runner-level OS isolation. Here, bare/safe/no-tools/no-session flags prevent repository or model
content from spawning them; the hosted runner remains the outer isolation boundary.

### The commit-time hygiene guard

This CI boundary is separate from the local hygiene guard. Truth findings never block a commit.
The guard inspects staged filenames against a narrow, high-signal block list (credential
filenames like `.env`/`*.pem`, OS cruft, AI-slop report names) — a secret or slop file outside
that list, such as `config.yaml`, isn't inspected or blocked. It also conservatively rejects a Bash tool call that combines
`git add` and `git commit`, because it cannot inspect the finalized index between them. It also
rejects commit modes such as `-a`/`--all`, `--include`, `--only`, and pathspec commits because they
can source unstaged working-tree content after inspection. Use a **separately staged plain commit**;
run staging and commit in **separate tool calls**. Deletion-only cleanup is allowed, and
`EVERGREEN_GUARD=off` is the explicit bypass.

## Trust and safe execution

Evidence providers and source maps are passive candidate inputs; Evergreen never executes provider commands or accepts their verdicts.
Executable proof is local and explicit; CI never executes pull-request code, and unsafe or unavailable isolation is inconclusive.

Winnow's default prove-by-test path is local: it uses a repository-declared test command, a
bounded timeout, and a disposable scratch location. It does not forward new secrets, refuses
privileged, destructive, deployment, upload, publication, and portal-mutation commands, and
disables network access when the host can do so safely. The classifier is only a conservative
first filter: “allowed” does not replace isolation, timeout, dependency, and permission checks.
Setup failures and timeouts are inconclusive, not proof of drift.

CI has a different boundary: it supplies delimited, bounded, exact-commit evidence to a semantic
reviewer with no tools, then independently validates schema, commit binding, counts, citations,
and runtime identity.
Repository content cannot change those instructions or the publication policy.

### Executable-oracle source pack

The separate [executable-oracle source-pack contract](../eval/oracle/README.md) is present but not yet
corpus-ready: no curated public source identities or external private custody package is claimed in
this tree.
