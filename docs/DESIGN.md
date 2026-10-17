# Evergreen — design & prior-art credits

Evergreen is a local semantic **skill** that makes the agent *notice* when docs and code have
drifted apart and prove it before flagging. A deterministic trust layer supports that judgment in
CI; it does not replace it with a general-purpose scanner or hosted service. The semantic work
still uses the model's optional local evidence (read, grep, diff, and scratch tests when needed).

This design is synthesized from a survey of **309 repos** (164 directly related, 79 of
them zero-star — the clever-but-unknown longtail). The repos were an **idea mine**: we
took techniques, taxonomies, and mental models and distilled them into the skill's
ruleset. Nothing here is reinvented where an accredited approach already exists; each
idea is credited to the repo it was mined from.

> **Design history (honest note):** an earlier iteration built a deterministic bash
> engine (grep/git path & contract verdicts, tree-sitter-style coverage, SARIF). That verdict
> engine was removed: it ported the *infrastructure* of the surveyed repos when the goal was to
> mine their *ideas* for the prompt. Today's smaller deterministic layer prepares and validates
> evidence; semantic truth judgments remain in the skill rather than a binary.

## Principles

- **Cheapest check that proves the drift** — mechanical rungs before semantic reasoning.
- **Intensity `off / light / strict`** — light walks rungs 1–3, strict adds the rung-4 semantic pass.
- **A local anti-doc-staleness reflex**, injected as behavior, with a deterministic CI boundary.

## The freshness ladder (the core behavior)

When code changes, the agent walks the rungs in order and stops at the first that holds.
The cheap, mechanical checks come before the semantic read — but the *agent* does them,
with the tools it already has (read the file, grep the repo, read the diff), citing the
code every time.

1. **Vanished paths** — an in-repo file path a doc names that no longer exists on disk,
   or was just renamed/deleted in the diff but is still cited. *(kedge, docs-drift-check, lychee)*
2. **Dead contracts** — a CLI flag, env/config key, function, route, or type a doc
   documents that no longer exists in the code. Code is the source of truth; the doc is
   the claim under test. *(doc-checks, readme-drift, sachn1)*
3. **Drifted snippets/signatures** — a fenced code block, signature, endpoint table, or
   config schema in a doc that no longer matches the source it describes. *(ifiokjr/mdt, docfresh)*
4. **Semantic drift** — only then: does the prose still describe what the code does now?
   Reasoned, with the code in front of you. *(driftcheck/kedge hybrid)*

Model routing, for hosts that tier it: cheap models for the mechanical rungs, stronger
ones for semantic behavior drift. *(xiaolai/docs-guardian)*

## Architecture (skill + trust layer + hooks + state)

The intelligence is the skill (`skills/evergreen/SKILL.md`) — the ladder and rules live in the
model's head. Three thin hooks make it ride along, and they never read or analyze doc *content*:

- **`evergreen-activate.sh`** (SessionStart) injects the mode preamble plus the condensed
  `DIGEST.md` (about two-fifths of the full skill by words, which stays loadable on demand).
- **`evergreen-mode-tracker.sh`** (UserPromptSubmit) is the *sole writer* of the intensity state. It
  matches a sigil form where it begins the first non-blank line or sits in the first sixty characters of the prompt,
  and an imperative form only inside that window, so text that merely quotes them never flips the mode.
- **`evergreen-stop.sh`** (Stop) is a post-turn audit request when code-with-docs changed; git/state
  guards only, always non-blocking, deduped to fire once per distinct change state (a signature in
  `.git/`, not on every turn while the tree sits dirty).

State is a per-repo `.evergreen-mode` file (`off|light|strict`, default `light`); the tracker
excludes it through `.git/info/exclude`, so a consumer repo never sees it as untracked. An
optional repo-local `.evergreen-ignore` lists patterns the *agent* honors when deciding what to
flag — there is no hook that parses it; the skill is the enforcement.

The PR Action adds a deterministic trust layer around the semantic reviewer. It produces a bounded
manifest plus matched living-document excerpts from regular Git blobs at the exact head,
JSON-escapes boundary characters, labels all repository material as **untrusted data**, and rejects
any result that fails schema, claim-inventory, commit, citation, or trusted-runtime checks. The CI
model has no tools or project customizations and receives only its provider credential. The
validator reads citations from Git at the audited head; model prose cannot certify itself. The
layer prepares and validates evidence but does not decide whether prose is semantically true.

The layer, not the model, fixes the review's denominator. Each matching documentation line becomes
one claim whose id hashes its path, line number, and text, and the driver keeps that inventory as
trusted input for the validator, never reading it back from model output. The model returns one
verdict per id; the validator rejects a missing, repeated, or unknown id and binds every finding
and unverified entry to the drift or unverified claim it cites. Claim counts are derived by the trust layer from one verdict per supplied claim id, never reported by the model.
Opt-in calibration controls (`calibration_controls`) add a known-certified exact quote and a
known-drift misattributed quote of head documentation. Every id in that run, real or control, is
rekeyed with a per-run nonce, so none can be recomputed from public fields, and the list is
shuffled from the same nonce. The validator applies its bounds and shape checks to control entries
too, grades the controls against a trusted answer key, strips them from every count, finding, and
unverified entry, and makes the run inconclusive when either is answered wrong. The renderer
redacts model text, citation paths included, that repeats a control's statement. A control is
still recognizable by its `reads exactly:` form, so a pass shows only that the reviewer checked the
supplied evidence for the controls. It does not show that the reviewer reviewed the real claims.

Every repository path in a manifest entry or result citation passes one shared gate before the
trust layer will carry it: `is_protocol_path` (`ci/path_policy.py`). A citable path is a non-empty
string of at most 1024 characters, relative (neither POSIX- nor Windows-absolute), forward-slash
only, already in normalized POSIX form, with no `.` or `..` segments and no newlines, carriage
returns, NULs, or Unicode surrogates. A path failing any rule is rejected as "not citable by the
result protocol"; the change manifest and review-context citations enforce the same policy.

POSIX timeouts terminate the pinned CLI's inherited process group. Portable standard-library code
cannot contain a deliberately detached descendant; runner-level OS isolation owns that boundary.
The Action's bare, safe, no-tools, and no-session modes prevent repository or model content from
creating such a descendant.

Command execution passes through a shared gate as well: `run_bounded` (`ci/bounded_process.py`),
which runs one command under wall-clock, output, process-group, and environment bounds. With
`clean_env` the child's environment is rebuilt from an allowlist — `PATH`, `HOME`, `TMPDIR`,
`LANG`, `LC_ALL`, plus caller-named keeps — rather than inherited, and stderr is discarded.
Its three bounded failures return instead of raising: a timeout returns exit 124, an output
overflow 125, a command that cannot start 126, and captured output is dropped on failure unless `preserve_partial` keeps the truncated
prefix. The process-group termination described above is enforced here.

CI outcomes are deliberately distinct: **complete and clean**, **complete with findings**, and
**complete with unverified** all mean the validated review finished; only the first is a clean
certification. **inconclusive** means the audit itself failed or could not be trusted. Findings
never fail the check. Inconclusive runs fail by default, while `fail_on_inconclusive: false` makes
infrastructure advisory without changing the rendered status.

The commit-time hygiene guard has a narrower boundary. It inspects the finalized staged index on a
commit-only call, blocks known secret/slop paths, and allows deletion-only cleanup. A single shell
call that combines staging and commit is rejected conservatively because PreToolUse cannot observe
the index between them. The same rule covers every index-writing subcommand (`mv`, `rm`, `apply`, `stash`, `restore`, `checkout`, `reset`, `update-index`, `read-tree`, `cherry-pick`, `revert`, `merge`, `rebase`, `am`) paired with a commit in one call. Commit modes that can add unstaged content (`-a`/`--all`, include/only, or
pathspec forms) are rejected for the same reason: use a **separately staged plain commit**.
Run staging and commit in **separate tool calls**. `EVERGREEN_GUARD=off` remains the explicit
bypass. Semantic truth findings and CI drift findings do not use this blocking path.

## Intensity, and why it is checkable

Intensity was an instruction and nothing more until findings recorded their provenance. Every
finding now names the rung that proved it: `path · contract · snippet · prose`. `prose` is the only rung
resting on judgment rather than a mechanical check, so it is the only one an intensity has reason
to withhold — light must never emit a `prose` finding, and an undeclared mode is itself a
violation. The rung travels into the CI result envelope, where the validator checks it against
its enum. Nothing stops a model mid-turn, the hooks deliberately never read output, and no local
checker re-reads a transcript. That boundary is the
honest limit of the claim.

## Shipped local surface

`bin/evergreen impact` and `bin/evergreen till` are the dependency-free, read-only query entry
points, and `bin/evergreen judge` is the one sanctioned external call: it packs a doc plus the
in-repo files it names into an isolated codex session for the second-provider verdict every
judging command must end with: prose in winnow, seed, and flourish, the surface inventory in seed
and till, the judgment rows in cultivate. Its block is carried in every report, the release gate refuses a
winnow or cultivate record without it, and it abstains rather than guessing. `impact` takes changed paths,
optional evidence JSON, and repository-local `.evergreen-map.json` mappings; `till` inventories the
tracked public declaration surface for seed and fails closed with a `truncated` warning whenever the
inventory it builds could be incomplete; files outside the parseable surface (unparsed languages,
extensionless scripts without a Python or shell shebang, untracked source) are surfaced with
`outside inventory` warnings rather than silently skipped; a Python-shebang script such as
`bin/evergreen` joins the parsed inventory, and a truncation warning names the cap that tripped.
Their human and JSON forms expose ranked candidates, reasons or scan counts, and warnings; they do
not expose semantic findings or verdicts.

No host installer ships. Claude Code and Codex load the plugin through their own plugin
mechanisms, other agents take `SKILL.md` or `AGENTS.md` directly, and nothing in the repository
writes to a home directory or installs into a host (temporary files and `.git/` state are expected).

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

Use `evergreen receipt --repo PATH` for the local snapshot. Local Git state cannot verify external
publication; without direct authority, external release state remains unverified.

The receipt command is a bounded, deterministic, read-only architecture seam. It reads local Git
metadata and, optionally, one validated in-repository benchmark-publication manifest; it writes no
source-repository, index, ref, configuration, or receipt file. Status collection uses disposable
synthetic Git metadata in the operating system's temporary directory and removes it afterward. It performs no network request and never
queries GitHub, a registry, a marketplace, a store, a deployment provider, or a benchmark provider.
No timestamp is emitted, so unchanged input has stable JSON. The human renderer presents the same
repository, release, and benchmark fields without adding interpretation. Local tags remain local
evidence, `release.external_state` remains `unverified`, and benchmark state is only
`declared_publication`; neither rendering claims fresh provider execution or external publication.
The Git reader forces rename, mode, and symlink visibility; it pins one non-split index for every
index-dependent read and collects status through temporary synthetic Git metadata outside the
repository, so repository configuration is not loaded. It refuses tracked
submodules, split indexes, hidden-index flags, and effective external clean/process filters, and
brackets both Git and captured-HEAD manifest identity to reject concurrent change. Git reads have one streaming output
cap/deadline with process-group cleanup; manifest bytes come through one no-follow descriptor.
Lazy object fetching is disabled for every Git call. Captured-HEAD identity uses a non-fetching tree
lookup followed by a bounded local blob read, so an unavailable promised blob is operationally
unverified rather than fetched or misreported as a missing manifest.

## Release identity boundary

Release identity spans package manifests, registry versions, and version-reporting CLI output.
Audit version-bearing badges, version-reporting installed-command examples, generated API version labels or headers, and deployed docs version labels as linked release claims.
Interpret each claim's meaning: current source and latest published release may legitimately differ.
Keep independently versioned packages and platforms as independent release streams unless repository policy explicitly couples them.
Without direct registry, store, or deployment evidence, report external release state unverified.
Never publish, upload, push, deploy, or mutate a portal or registry without explicit user authority.

The checked-in manifest owns current-source identity unless the repository declares another source.
A version-bearing surface must also declare what it means. Compare current-source claims with the
manifest and latest-published claims with direct registry/store/deployment evidence; do not call
their expected difference drift. Generated API documentation is reconciled through its generator
or source, not silently hand-edited. A local tag, badge, or release note cannot certify public state.

Monorepos need an explicit coupling rule. Related deliverables in one stream align; independently
published packages do not receive synchronized bumps merely because they share a repository. Apple
apps retain the distinct marketing-version/build-number rules, monotonic binary builds, Universal
Purchase alignment, and external-store uncertainty described by the skill.

### 0.4.0 release decision

The Claude manifest, Codex manifest, and marketplace entry are one coupled plugin release stream.
Since `0.3.2`, that stream gained the impact CLI, passive provider and source-map inputs, reversible
Claude/Codex setup and diagnostics, bounded executable proof, generalized release identity, and
execution-policy hardening. These are backward-compatible pre-1.0 features, so the repository's
SemVer policy justifies minor `0.4.0`, not patch `0.3.3` or stable `1.0.0`. This plugin stream has
no binary build-number field, and the release invents none.

### 0.5.0 release decision

Since `0.4.0`, the coupled plugin stream gained the creation axis: the `seed` command with its
frozen hard-goal contract and owner-decided write batches, the deterministic `gaps` surface
inventory CLI it fails closed on, and the declaration-grammar extensions that back both. These are
backward-compatible pre-1.0 features — no existing command, output shape, or hook contract
changed — so the same SemVer policy justifies minor `0.5.0`, not patch `0.4.1` or stable `1.0.0`.
The stream still carries no build-number field.

### 0.6.0 release decision

`0.6.0` renames the surface-inventory subcommand `gaps` → `till`, completing the family metaphor
(till the ground, then seed it). A renamed public CLI subcommand is a surface change, so the same
pre-1.0 SemVer policy takes minor `0.6.0`; the rename shipped immediately after `0.5.0`, before
any external adoption of the old name, so no compatibility alias is carried. Frozen `0.4.0`/`0.5.0`
records keep the names they shipped with.

### 0.6.1 release decision

`0.6.1` is a patch, and it is the one release in this stream whose number does not follow from the
rule above — so the reasoning is recorded here rather than left for a later reader to reconstruct.

Since `0.6.0` the stream gained a new CLI subcommand, `conform`, which holds a reflex transcript to
the intensity it declares; findings gained a `rung` field naming which ladder rung proved them; the
`light` intensity became falsifiable rather than advisory; `flourish` gained two counted hard goals
and a mermaid diagram requirement; and the Bash guard gained the release-tag certification gate
below. By the precedent set at `0.5.0` — backward-compatible pre-1.0 feature additions justify a
minor, not a patch — and reinforced at `0.6.0`, where a subcommand *rename* alone took a minor, this
set would ordinarily take `0.7.0`.

It ships as `0.6.1` by owner decision. The rule is not being amended: `conform` is an additive
surface with no consumer, `rung` is an added field on an existing envelope rather than a changed
shape, and no existing command, output shape, or hook contract changed — so nothing here is a
compatibility event, and the number understates the release rather than misdescribing it. Treat
`0.5.0`'s rationale as still governing the next release that adds surface; this entry is the
exception with its reason attached, not a new precedent to cite.

The stream still carries no build-number field.

### 0.7.0 release decision

Since `0.6.1` the stream gained a new CLI subcommand, `judge`, and a Claude Code command,
`/evergreen:mode`, replacing a `.toml` file Claude Code never loaded; the guard's compound rule
widened to fifteen index-writing subcommands; `conform` rejects unparseable finding lines; the
impact and till grammars changed (Python constants, shell functions, Rust and Swift visibility,
multi-line imports); every eval scorer enforces its hard goals, and a fifth suite joined the
definition of green. It also removed two shipped surfaces: `grade verify`, which could not return
an earned grade for any input and had no caller, and the reversible host `install`, `doctor`, and
`uninstall` commands, which duplicated what both hosts' own plugin mechanisms already do, and the
three Gemini-style `.toml` command files no host manifest referenced. The additions justify a minor by the `0.5.0` precedent; the
removals are pre-1.0 surfaces with no external consumer, recorded here rather than taking a major.
So `0.7.0`, not `0.6.2` or `1.0.0`. The Python floor is 3.10 for every subcommand except `judge`,
which needs 3.11 for `tomllib`. The stream still carries no build-number field.

### 0.8.0 release decision

`0.8.0` removes `conform`, a public subcommand that re-read a transcript nobody saved, and makes the
second-provider judge mandatory rather than invocable: winnow, seed, and flourish end with it over
prose, the seed and till commands run it over the surface inventory (the CLI's `till` subcommand
only prints that inventory), cultivate runs it over its judgment rows,
its block is carried in every report, the eval scorers refuse a report without it, and the release
gate refuses a tag unless both certification records carry a `judge` object. A deterministic inventory
is judgment frozen into grammar — till's Rust and Swift visibility rules were wrong until 0.7.0 and
nothing caught it (its tests now pin them) — which is why the judge covers it too. Removing a
public subcommand and changing the record the gate reads are surface changes, so the `0.6.0`
precedent takes a minor. So `0.8.0`, not `0.7.1`. The stream still carries no build-number field.

### 0.8.1 release decision

`0.8.1` changes no surface. The two exam runners grant the judge entry point their tasks already
demanded and name it; the winnow exam gates the judge row's presence and coverage rather than its
drift count, because its fixture seeds drift on purpose; the seed exam's answer key carries the
module constant till lists; the fixture CLI honours the `--verbose` its doc promises; and
the 0.8.0 winnow ledger was re-audited so that every certified row cites the one line that makes it
true, with instructions, rationale, and claims about other systems recorded as dropped rather than
counted, and nothing left unverified. Evaluation, fixture, and evidence fixes with no public subcommand, record
shape, or command contract changed take a patch under the `0.5.0` precedent. So `0.8.1`, not
`0.9.0`. The stream still carries no build-number field.

### 0.8.2 release decision

`0.8.2` corrects the README's install and Action sections to the repository's real exposure. The
repository is private, so every route starts from read access: a clone over SSH added as a local
marketplace or loaded with `--plugin-dir`, and an Action that resolves only from repositories GitHub
lets reach a private one. A doc describing a public install path for a private repository is drift
of the kind this tool exists to catch, not an owner's choice: the docs follow the setting, the
setting does not follow the docs. A documentation-only correction with no surface change takes a
patch under the `0.5.0` precedent. So `0.8.2`, not `0.9.0`. The stream still carries no build-number
field.

### 0.9.0 release decision

`0.9.0` adds surface. The PR Action's model envelope moves to `schema_version` 3: the trust layer
enumerates one claim per matching documentation line and derives every count from one verdict per
supplied claim id, and the Action gains an opt-in `calibration_controls` input. `till` admits Java,
the winnow exam gains an `attack` kind, and the benchmark cascade gate now judges the natural 10/90
base rate rather than the balanced probe. The commit-hygiene guard and release-tag gate close
bypasses without changing what they refuse by design. New inputs, a new envelope schema, and a new
inventory language are additions, so the `0.5.0` precedent takes minor `0.9.0`, not patch `0.8.3`
or stable `1.0.0`. The stream still carries no build-number field.

### Winnow-before-tag certification gate

A local `evergreen--v*` tag is a certification claim about the commit it names, so the PreToolUse
Bash guard refuses to create one until `hooks/evergreen-release-gate.py` verifies two checked-in
receipts, both read from the HEAD tree and never from the working directory:
`eval/certifications/winnow.json` — a JSON object carrying `pass: "winnow"`, a `date`, the full
40-hex `commit` the pass ran at, and integer `claims` counts that must report zero `drift`, zero
`unverified`, and `certified + drift + unverified == total`, plus a `judge` object (`status` ok, a
`provider`, `docs`, `certified`, `drift` 0, `unverified`) — and
`eval/certifications/cultivate.json`, the same shape without claim counts (`pass: "cultivate"`,
`date`, `commit`, and the same `judge` object), because cultivate never reports "clean"; its record
asserts cadence and a judged report, not cleanliness.
Each record's commit must be HEAD itself or an ancestor of HEAD from which nothing outside
`eval/certifications/` has since changed, so committing the records is the only history permitted
between running a pass and tagging. The ritual is therefore: commit the release work, run the winnow
and cultivate passes at that commit, write each record naming it, commit the records, then tag. Tag
deletion, listing, and verification pass through unclassified; a creation naming an explicit
committish other than `HEAD`, a creation combined with `git add`/`git commit` in one call, and a run
where python3 cannot verify the records are each refused rather than guessed at, while
`EVERGREEN_GUARD=off` remains the deliberate operator bypass. The gate verifies record arithmetic
and commit binding only — that a record honestly reflects a pass that ran is the runner's
responsibility, and a tag created outside a hook-carrying session is not intercepted. This is a
freshness-and-cadence gate on the documentation and hygiene axes, not a detector-quality gate: it
neither claims nor requires the measured-detector evidence that the measurement-first boundary below
reserves for quality thresholds.

### Measurement-first quality and release boundary

The audit, replay, resolver-v2, Java-context, and public-verification infrastructure is additive,
but it does not by itself prove improved shipped detection. Before another certification framework
or quality threshold becomes a release gate, the current detector must be measured on declared,
immutable inputs with its candidate revision, evaluator, source revisions, mutation protocol, and
result identity fixed before execution. Thresholds may then be proposed for a future run; they may
not be retrofitted to turn an observed result into an A.

The unfinished executable-oracle certification proposal is deferred and is not an active release
gate. Its useful in-project mutation pilot and pinned source catalogs are research inputs, not
evidence that a corpus, five-language run, A grade, or best-in-class result exists. A future version
is chosen from shipped product scope and completed evidence, not from the amount of certification
machinery written.

The decision package under `eval/bench/public/0.4.0` and `eval/bench/results-0.4.0.md` are a separate,
immutable evaluated-release identity. A later source-version bump must not rename them, inherit
their metrics, or imply that resolver v2 or an executable-oracle candidate passed measurements that
were not run. Until direct evidence exists, human-label validity, current detector quality,
marketplace publication, and other external release state remain unverified.

## Hybrid provider boundary

Provider evidence and source maps nominate candidates, never findings or verdicts.
Re-read every candidate against current code before deciding drift.

The read-only `bin/evergreen impact [--repo PATH] [--evidence FILE] [--json] PATH...` command
combines changed paths, validated provider facts, and repository-local source maps into a ranked
candidate list. Provider confidence affects ranking only. A map broadens the candidate set; it
cannot suppress changed-path or normal grep baseline candidates, and it cannot certify a document.
Invalid records and maps remain warnings rather than semantic conclusions.

Evidence providers and source maps are passive candidate inputs; Evergreen never executes provider commands or accepts their verdicts.

This boundary supports Drift-shaped interoperability through adapters that translate mechanical
facts into the v1 evidence schema. It deliberately does not import an external tool's finding or
verdict. The fixture demonstrates why: a default changing from 60 to 30 can nominate timeout docs,
but the per-project override remains true because `setdefault` preserves configured values.

## Safe executable proof boundary

Executable proof is local and explicit; CI never executes pull-request code, and unsafe or unavailable isolation is inconclusive.

Local execution uses only repository-declared tests with a bounded timeout and disposable scratch
state. It refuses privileged, destructive, cleanup, deployment, upload, publication, and portal
mutation; it does not add or forward secrets. Network access is disabled when the host can do so
safely. A classifier may reject a command early, but an allowed shape still requires trusted
isolation, permissions, dependencies, and setup. Timeout or setup failure never becomes drift.

The PR Action reviews bounded, exact-head pull-request evidence as untrusted data but does not
execute the changed project or give the model repository tools. Its deterministic boundary
validates the model's result before rendering or applying the configured inconclusive policy.

## Evaluation status

Current five-language benchmark metrics are published only from one compatible run that clears every declared coverage gate.
The frozen Evergreen 0.4.0 five-language run is a replayable historical execution record, not valid
performance evidence. Its artifacts share one implementation, judge, dataset hashes, model/CLI
identity, protocol, and settings, and all five languages cleared the declared 99% provider-completion
gate. However, canonical IDs exposed label-construction proxies to both the judge and pre-fix label
screen, so the recorded accuracy metrics are contaminated and unverified — the completion coverage itself is not in question. The current protocol masks
canonical IDs, fails closed on incomplete screens, and binds launch input to exact dataset bytes.
For screened v2 lanes it also recomputes the two-of-three retained set from the complete vote
ledger, requires the split/parent manifests and selection receipt to match tracked `HEAD` bytes,
and records the receipt hash in the paid artifact. Resolver v2 has no clean published result yet.
Interrupted, provisional-label, development, or
cross-commit diagnostics remain diagnostic; normal CI runs only offline verification, never
provider-backed scoring or publication.

## Non-goals

Evergreen is not a hosted index, AST engine, dashboard, or automatic truth-path prose rewriter.

It does not bundle language parser suites, accept provider verdicts, infer truth from checksums,
execute untrusted provider or pull-request commands, automatically rewrite semantic prose, or
publish incomplete evaluation results. Hosted services, embeddings, dashboards, chat integrations,
and portable container/VM orchestration remain outside this release.

## Drift taxonomy (so findings are actionable)

Every finding is one of `in_code_not_docs · in_docs_not_code · name_mismatch ·
release_identity_drift · UNVERIFIABLE` (a claim about another system — drop it, don't guess).
*(NathanMaine/memoriant-docforce,
Tenormusica/doc-freshness-analyzer)*

Prose/comment rot lenses, each verifiable against the code: `contradiction ·
stale-reference · signature-mismatch · outdated-example · resolved-marker ·
orphaned-comment`. *(Jan-ARN/drift)*

Each finding carries a severity and an explicit **fix-or-flag** call. *(Zarl-prog/doc-drift-detector)*

## Rules that keep it trusted

- **Prove it or drop it** — cite the code, or it isn't a finding; an adversarial second
  look kills plausible-but-wrong flags. *(Jan-ARN/drift skeptic pass)*
- **Rot lives in old comments, not new lines** — read the changed file at HEAD, not just
  the diff's `+` lines. *(Jan-ARN/drift)*
- **Editing is not verification** — a file touch must not reset staleness; only a check
  against the code clears it. *(ddpoe/axiom-graph: sticky staleness)*
- **Code is the source of truth, the doc is the claim** — documented-but-missing is
  failure; existing-but-undocumented is informational. *(MarekWadinger/doc-checks)*
- **Exempt what leads or freezes code** — specs/ADRs/RFCs/roadmaps/plans lead; audit/
  readiness/archive/dated snapshots and CHANGELOG history freeze. Never gate either.
- **Noise blocklist + learnings ledger** — third-party tool flags, CSS custom properties,
  URLs, generic symbols are not your contracts; a rejected flag never returns.
  *(sachn1/readme-drift, drift)*

## The fix half — generate vs review

The generate-vs-review line *(docugardener / ArjunVenat / Sintesi synthesis)*:

- **Propose a diff** for what's 1:1 derivable from code — a dead reference (renamed/removed
  path, flag, env key), an endpoint table, a type/enum/config schema, a snippet that
  should mirror its source. The human applies it.
- **Flag, never rewrite** what has no deterministic anchor — a changed signature,
  architecture rationale, tutorials, "how it works", the security model, the *why*.

## What stays homegrown

The **semantic claim assertions** ("this prose fact about the code still holds") are
project-specific — no off-the-shelf idea owns your repo's knowledge. That's the slice
the model's judgment fills, guided by everything above.

## Source ideas, in brief

Mining notes live under `.research/` (gitignored). Beyond the credits inline above:
six-lens rot taxonomy and pre-filter-before-the-model *(Jan-ARN/drift)*; "code is truth,
doc is claim" asymmetry *(MarekWadinger/doc-checks)*; sticky staleness *(ddpoe/axiom-graph)*;
coverage-as-a-score thinking *(econchick/interrogate, epassaro/docstr-cov-workflow)*;
embed-from-source and SHA-pinning as the *concepts* a human can apply by hand
*(ifiokjr/mdt, os-tack/docfresh)*; staleness-by-age was evaluated and **rejected** — age
is a weak proxy; evergreen flags only what it can cite against the code *(e4we/doc-staleness)*.
