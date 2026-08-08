<h1 align="center">🌲 Evergreen</h1>

<p align="center">
  <em>The docs said yes. The code said no. Only one of them gets to be true.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/local%20skill-trusted%20CI-111111?style=flat-square" alt="Local skill with trusted CI">
  <img src="https://img.shields.io/badge/works%20in-any%20language-111111?style=flat-square" alt="Any language">
  <img src="https://img.shields.io/badge/checked-against%20the%20code-111111?style=flat-square" alt="Checked against the code">
  <img src="https://img.shields.io/badge/license-MIT-111111?style=flat-square" alt="MIT license">
</p>

<p align="center">
  <strong>Cites the line or says nothing &middot; rewrites nothing unasked &middot; any language</strong><br>
  <sub>A documentation-freshness reflex for AI coding agents.</sub>
</p>

---

Your README was true the day you wrote it. Then a flag got renamed, a file moved, a function started returning something else — and the docs stayed exactly where they were. That's how documentation lies: not by being wrong when written, by being *left behind*. The gap opens quietly and nobody sees it until someone pastes a command that no longer exists.

Evergreen is a local semantic skill, backed in CI by a deterministic trust layer. After a change lands, it reads the affected docs back against the source and surfaces only what it can cite as gone false — pointing at the exact line. It rewrites nothing on its own. It just refuses to let the docs and the code disagree in silence.

## Before / after

You rename a flag and move on. Three files still document the old name. Nobody notices until someone copies a broken command.

With evergreen, in the same turn:

```
evergreen: you renamed --workers to --concurrency.
  README.md:42   documents --workers — gone from cli.py → fix
  docs/cli.md:8  same flag, same fix
left alone: docs/adr/0003.md mentions --workers — an ADR, frozen in time.
```

It cites the line or it says nothing. And it leaves the docs that are *meant* to describe the past — ADRs, specs, dated snapshots — alone. They lead the code; they don't lie about it.

Your agent already has an *update the docs if applicable* step. [That's an intention, not a procedure](#i-already-tell-my-agent-to-update-the-docs).

## Install

### Claude Code

```
/plugin marketplace add ChrisPachulski/evergreen
/plugin install evergreen@evergreen
```

It rides along every session: adds `/evergreen:winnow`, and — after a turn that changed code in a repo with tracked docs — leaves a quiet nudge to go check for drift. Intensity is `off | light | strict` (default **light**). The truth reflex never blocks your commit; it flags, you decide.

### Any other agent

The whole skill is [`skills/evergreen/SKILL.md`](skills/evergreen/SKILL.md). Drop it into any skill-capable agent, or paste it into your system prompt. For Codex, Copilot, Gemini, and anything that reads [`AGENTS.md`](AGENTS.md), the flat-prose ruleset already lives at the repo root.

### No install at all

Rank the docs a change puts at risk, with nothing but Python 3.10+ and Git:

```sh
./bin/evergreen impact --repo . path/to/changed-source.py
./bin/evergreen receipt --repo .
```

### On every pull request

```yaml
# .github/workflows/evergreen.yml
on: pull_request
permissions: { contents: read, pull-requests: write }
jobs:
  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4.3.1
        with: { fetch-depth: 0 }
      - uses: ChrisPachulski/evergreen@9abbdd954cbce16b4107c58ca653db3c9f0cb351 # immutable 0.6.0 Action runtime (evergreen--v0.6.0)
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          fail_on_inconclusive: true
```

Drift never fails the build. The full CI contract — what a green check certifies, the four outcomes, fork-PR behavior, and every process bound — is in [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

<details>
<summary>Host setup, platform support, and the reversible CLI install</summary>

Candidate queries require Python 3.10+ and Git. Host management requires Python 3.11+ and is
supported on macOS and Linux: install, doctor, and uninstall rely on POSIX locks, symlinks, file
modes, atomic rename, metadata copying, and directory `fsync`. The semantic skill remains
language-agnostic, but the bundled host-management CLI does not currently support Windows.

The local CLI can wire the canonical skill into either host while preserving existing instructions:

```sh
./bin/evergreen install --host claude
./bin/evergreen install --host codex
./bin/evergreen doctor --host all --repo .
./bin/evergreen uninstall --host all
```

Add trusted, passive provider facts to a candidate query when available:

```sh
./bin/evergreen impact --repo . --evidence examples/provider-evidence.json eval/fixture/config.py
```

Instruction files and their rollback snapshots are limited to 1 MiB, ownership records to 4 KiB, and
each plugin manifest to 64 KiB; sparse files are checked by logical size. Full transaction
semantics, lock and journal behavior, and what `doctor` will and won't touch are in
[`docs/OPERATIONS.md`](docs/OPERATIONS.md).

</details>

## How it works

When code changes, it stops at the first rung that catches:

```
1. A doc names a file that's gone?      → grep, confirm, flag
2. A documented flag / env / route gone? → grep the code, flag
3. A shown snippet drifted from source? → read both, compare
4. Does the prose still tell the truth? → only then, reason
```

One rule above all: **prove it or drop it.** If it can't cite the code that makes the doc wrong, it isn't a finding. A checker that cries wolf gets muted — that rule is the muzzle.

## "I already tell my agent to update the docs"

Most people do. One line in a workflow file — *update documentation if applicable* — and it feels like it covers this. It doesn't, because it's an intention, not a procedure. Four gaps:

- **"If applicable" is a scope decision, and scope is the whole problem.** The step fires against whatever files are already open. The renamed flag sits in three files the agent never touched this turn. Evergreen runs the other direction: start at the change, walk *outward* to the docs that name what you touched.
- **No proof rule, so you get both failure modes.** With nothing to prove, an agent either shrugs (nothing looked obviously wrong) or helpfully rewrites prose it never verified — inventing accuracy, which reads more confident than the stale line it replaced. Evergreen's rule is the product: cite the code that makes the doc false, or it isn't a finding.
- **"Update the docs" has no idea which docs are *supposed* to be out of date.** ADRs, specs, dated snapshots, changelog history — those describe the past on purpose. A generic instruction will cheerfully correct a decision record into a lie about what you decided, and nobody catches it, because it looks like housekeeping.
- **Nothing fires it, and nothing checks it.** A workflow line runs when the agent remembers it. Evergreen's Stop hook fires when code changed in a repo that has docs, once per distinct change state. In CI the validator reads every citation back out of Git at the audited commit, so model prose can't certify itself.

The honest version: solo on one repo with one README you wrote last week, your one-liner is fine. This earns its keep when the docs are plural, when strangers paste your commands, and mostly when *agents* write the code — an agent never feels the embarrassment of shipping a broken quickstart, so the reflex has to be wired in rather than requested.

More of what it catches, one per rung, in [examples/](examples/).

## Trust and safe execution

Evidence providers and source maps are passive candidate inputs; Evergreen never executes provider commands or accepts their verdicts. Executable proof is local and explicit; CI never executes pull-request code, and unsafe or unavailable isolation is inconclusive.

Winnow's default prove-by-test path is local: it uses a repository-declared test command, a bounded timeout, and a disposable scratch location. It does not forward new secrets, refuses privileged, destructive, deployment, upload, publication, and portal-mutation commands, and disables network access when the host can do so safely. The classifier is only a conservative first filter: "allowed" does not replace isolation, timeout, dependency, and permission checks. Setup failures and timeouts are inconclusive, not proof of drift.

CI has a different boundary, and the full contract is in [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## Commands

Four axes — **truth · craft · hygiene · creation** — one creed: prove it or drop it, you keep the final call.

| Command | What it does |
|---------|--------------|
| `/evergreen [off \| light \| strict]` | Set the intensity for this repo. No argument reports the current one. |
| `/evergreen:winnow [base-ref]` | **Truth, deep.** Walk every claim that changed since a ref and *certify it true or surface it* — silence means certified, not just "no lie found." Always strict. Prove-by-test is the default, not a flag: where the code runs and the safety boundary holds, a behavioral claim reading can't settle is settled by execution (write the test the doc implies, run it) — fails → drift proven, passes → certified by test; a refused or inconclusive run falls back to `behavior-asserted — verify manually`. |
| `/evergreen:flourish <file> [--all] [--manual]` | **Craft.** Rewrite an accurate-but-ugly doc to a gold standard (mined from 28 top READMEs), then prove every claim against the code. Emits a diff — never a silent overwrite. The only sanctioned prose-rewrite. |
| `/evergreen:cultivate [path]` | **Hygiene.** Local-only files leaking into git, gitignore gaps, AI-slop that shouldn't be tracked or public. Proposes untrack/ignore/delete — never auto. A commit-time guard backstops it (the one thing that *blocks*). |
| `/evergreen:seed [path]` | **Creation.** From a symbol-level surface inventory (fail-closed without one), triage everything worth documenting, recommend a batch, and write only what the owner approves — each doc ≤ 60 lines, earning its place with a behavior the signature alone can't show, every sentence code-backed and winnow-certified at birth; what the code can't settle is markered for the author, never invented. Purely additive and approval-gated. |
| `/evergreen:impact [--repo PATH] [--evidence FILE] PATH...` | **Truth, candidate query.** Find additive documentation candidates before editing changed paths. Read-only; never emits findings or verdicts. |
| `/evergreen:till [--repo PATH] [PATH...]` | **Creation, surface inventory.** Inventory the undocumented-surface candidates — every public declaration reachable from outside its file. Read-only. |
| `bin/evergreen impact [--repo PATH] [--evidence FILE] [--json] PATH...` | **Truth, candidate query.** Rank documentation related to changed paths and optional provider evidence. Read-only; never emits findings or verdicts. |
| `bin/evergreen till [--repo PATH] [--json] [PATH...]` | **Creation, surface inventory.** Deterministic ranked inventory of every declaration in its parsed surface (Python, Go, Rust, Swift, JS/TS) reachable from outside its file — the fail-closed provider behind `/evergreen:seed`. Read-only; scan incompleteness fails closed with a `truncated` warning, and files outside the parsed surface are named in `outside inventory` warnings. |
| `bin/evergreen receipt [--repo PATH] [--benchmark-manifest PATH] [--json]` | **Operational evidence.** Emit deterministic local repository, release-boundary, and optional declared benchmark identity without network access or mutation. |
| `bin/evergreen grade verify --repo PATH --manifest PATH [--json]` | **Operational evidence, gate.** Re-derive an A grade from a committed evidence manifest against the policy frozen in the subject commit. The manifest supplies observations only — a self-asserted `grade`, a threshold override, or bytes that differ from the captured HEAD are refused. Read-only; exit 0 only on a derived `A`, 1 when `inconclusive`, 2 otherwise. |

## How it's checked

That rule applies to evergreen itself. The [eval](eval/) seeds a fixture repo with catalogued lies, true claims that must not be flagged, and exempt docs, then lets a headless agent winnow it blind. The per-pair harness ([`eval/bench/`](eval/bench/)) runs the judge over labeled code/doc pairs. The [flourish eval](eval/flourish/) turns the craft command's own monstrosity test into machine-checkable gates: trapped fixtures where a beautiful gutting, a fabricated feature, or a flattened hook each trip a deterministic scorer that survived its own adversarial review.

**On the benchmark, once and plainly:** evergreen does not currently publish a trustworthy accuracy number. The five-language run completed 2,103 of 2,104 pairs and is replayable, but its judge received canonical IDs that leak label-construction proxies — that invalidates the accuracy metrics, though not the completion coverage. Clean runs now hide canonical IDs, fail closed on incomplete screening, and require exact dataset-byte binding before launch. Matrices, provenance, and the rerun plan: [`eval/bench/results-0.4.0.md`](eval/bench/results-0.4.0.md).

The separate [executable-oracle source-pack contract](eval/oracle/README.md) is present but not yet corpus-ready: no curated public source identities or external private custody package is claimed in this tree.

<details>
<summary>Re-deriving an A grade from a committed evidence manifest</summary>

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

</details>

## Non-goals

Evergreen is not a hosted index, AST engine, dashboard, or automatic truth-path prose rewriter.

- It does not ship language-specific parser suites, embeddings, a SaaS backend, or chat integrations.
- It does not turn checksums, changed constants, provider confidence, or source maps into semantic
  verdicts.
- It does not run commands supplied by provider files or untrusted pull requests.
- It does not claim category leadership, or present a matrix as certified evidence, before the
  declared five-language gate passes.
- It does not publish, deploy, upload, or mutate registries and portals without explicit authority.

## FAQ

**Will it rewrite my prose?**
Not unless you ask. The reflex points; you write — a dead flag or moved path it hands you a diff for, the *why* behind a design it won't touch. The exceptions are invoked deliberately: `/evergreen:flourish` crafts an existing doc to the gold standard, and `/evergreen:seed` writes docs where none exist — both instruct the agent to run a verification pass against the code before handing back output, though no hook or deterministic gate enforces that the pass actually ran. Fact-checker by default; ghostwriter only on request — and one that cites its sources.

**Won't it cry wolf?**
It flags only what it can cite against the code. Git's flags, CSS variables, other repos' paths, your ADRs — not its business. Tell it to drop something once and it offers the `.evergreen-ignore` line that keeps it dropped in every session after. The asymmetry is the product choice: a false flag costs you the ten seconds it takes to read the cited line and say no, while missed drift costs whoever trusts the doc next — which is why every flag must carry evidence you can dismiss at a glance.

**Does it scale?**
It reads paths, contracts, and prose — not your AST. Any language, any repo, nothing to compile.

**What about releases?**
It treats a shipped marketing version as a living public claim, distinct from the build number, and reconciles the surfaces that repeat that identity. Rules in [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

**Why "evergreen"?**
A doc that stays true as the code grows is evergreen. Yours aren't. Yet.

## Documentation

- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — release identity, receipt policy, evidence boundary, host-install transactions, the full CI contract, trust and safe execution.
- [`docs/DESIGN.md`](docs/DESIGN.md) — the freshness ladder, architecture, and prior-art credits.
- [`skills/evergreen/SKILL.md`](skills/evergreen/SKILL.md) — the whole ruleset the agent runs.

## Credits

Distilled from a survey of 309 repos — an idea mine, not a blueprint. The taxonomies and instincts behind the skill are credited to their sources in [`docs/DESIGN.md`](docs/DESIGN.md).

## License

[MIT](LICENSE). Keep the docs honest; do what you like with the code.
