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

Evergreen is a local semantic skill backed in CI by a deterministic trust layer. The moment your
agent touches code, it reads the affected docs back against the source and surfaces only what it
can prove has gone false — pointing at the exact line. On release work it also treats the shipped
marketing version as a living public claim, distinct from the monotonically increasing binary
build number. It rewrites nothing on its own. It just refuses to let the docs, release identity,
and code disagree in silence.

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

More of what it catches, one per rung, in [examples/](examples/).

## How it works

When code changes, it stops at the first rung that catches:

```
1. A doc names a file that's gone?      → grep, confirm, flag
2. A documented flag / env / route gone? → grep the code, flag
3. A shown snippet drifted from source? → read both, compare
4. Does the prose still tell the truth? → only then, reason
```

For releases, it follows each package or platform's checked-in version source, then reconciles the
surfaces that repeat that identity.

Release identity spans package manifests, registry versions, and version-reporting CLI output.
Audit badges, installed-command examples, generated API docs, and deployed docs labels as linked release claims.
Keep independently versioned packages and platforms as independent release streams unless repository policy explicitly couples them.
Without direct registry, store, or deployment evidence, report external release state unverified.
Never publish, upload, push, deploy, or mutate a portal or registry without explicit user authority.

For Apple apps, the existing rules remain: audit product milestones since the marketing version
last changed, advance the binary build monotonically, and verify related app/extension targets
resolve the same release identity. See the [package mismatch example](examples/package-release-identity.md)
for a non-app stream whose registry and deployment state remain deliberately unverified.

One rule above all: **prove it or drop it.** If it can't cite the code that makes the doc wrong, it isn't a finding. A checker that cries wolf gets muted — so this one never does.

The semantic pass may gather optional local evidence with read, grep, diff, or a scratch test. In CI,
the deterministic trust layer does the mechanical work: it binds a bounded change manifest to the
base and head commits, validates counts and citations against Git at that head, enforces runtime
identity, and renders only a valid result envelope. Repository files, diffs, paths, and comments
are **untrusted data**; instructions embedded in them never change the audit or publication rules.

### Hybrid evidence boundary

Provider evidence and source maps nominate candidates, never findings or verdicts.
Re-read every candidate against current code before deciding drift.

`bin/evergreen impact [--repo PATH] [--evidence FILE] [--json] PATH...` is a read-only candidate
query. It accepts records described by [`evidence-provider-v1.schema.json`](schemas/evidence-provider-v1.schema.json)
and repository-local source maps, ranks likely documentation, and reports malformed inputs as
warnings. Deterministic confidence means the provider proved its mechanical fact; it does not prove
a documentation claim false. Drift-shaped adapters may translate mechanical facts into this
schema, but provider-supplied findings and verdicts are rejected at the boundary. See the
[`provider-evidence.json`](examples/provider-evidence.json) sample and the
[semantic false-positive example](examples/provider-boundary.md).

## How it's checked

That rule applies to evergreen itself. The [eval](eval/) seeds a fixture repo with catalogued lies, true claims that must not be flagged, and exempt docs, then lets a headless agent winnow it blind. The per-pair harness ([`eval/bench/`](eval/bench/)) runs the judge over labeled code/doc pairs.

The corrected judge's current publication requires complete Python, Java, TypeScript, Rust, and Go
artifacts to individually clear one declared generated-report coverage threshold. That run was
interrupted by a provider session limit, and implementation commits landed between language starts.
Those diagnostic checkpoints have incompatible provenance and will not be resumed or published, so
no partial matrix is presented as a current result. The fresh five-language run will start from one
stabilized commit; protocol, dataset provenance, and publication status are in
[`eval/bench/`](eval/bench/).

## Install

### Claude Code

```
/plugin marketplace add ChrisPachulski/evergreen
/plugin install evergreen@evergreen
```

It rides along every session: flags drift the moment a change leaves a doc lying, adds `/evergreen:winnow`, and leaves a quiet nudge if you changed code and forgot to look. Intensity is `off | light | strict` (default **light**). The truth reflex never blocks your commit — it flags, you decide. (The hygiene guard is the one exception, and it's the kind you want — see [Commands](#commands).)

What it costs, since you count tokens: session start injects a ~40-line [digest](skills/evergreen/DIGEST.md), not the full ruleset (that loads on demand), and the post-turn nudge fires once per new change — not on every turn while the tree sits dirty.

### On every pull request

Want the check in CI too? Add the Action — it winnows the docs the PR's code touched, writes the
step summary, and upserts its bot-owned report comment (creating a replacement if an update fails).
Drift never fails the build. Under the default fail-closed policy, a green check means the requested
review actually completed; advisory `fail_on_inconclusive: false` runs can be green while still
reporting an inconclusive audit.

```yaml
# .github/workflows/evergreen.yml
on: pull_request
permissions: { contents: read, pull-requests: write }
jobs:
  docs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: ChrisPachulski/evergreen@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          fail_on_inconclusive: true
```

The outcomes are explicit:

- **complete and clean** — the validated review finished with no drift and no unverified claims.
- **complete with findings** — proven drift is reported, but the Action still exits successfully.
- **complete with unverified** — the review finished, but it names claims the available code could
  not settle; this is reported and is not a clean certification.
- **inconclusive** — the audit itself could not be trusted or completed, such as malformed output,
  truncated evidence, invalid citations, missing credentials, or a tool failure. This fails by
  default. Set `fail_on_inconclusive: false` for advisory-only infrastructure behavior; the report
  still says inconclusive and never pretends to be clean.

This CI boundary is separate from the local hygiene guard. Truth findings never block a commit.
The guard blocks staged secrets/slop and conservatively rejects a Bash tool call that combines
`git add` and `git commit`, because it cannot inspect the finalized index between them. Use
**separate tool calls**; deletion-only cleanup is allowed, and `EVERGREEN_GUARD=off` is the explicit
bypass.

### Any other agent

The whole skill is [`skills/evergreen/SKILL.md`](skills/evergreen/SKILL.md). Drop it into any skill-capable agent, or paste it into your system prompt. For Codex, Copilot, Gemini, and anything that reads [`AGENTS.md`](AGENTS.md), the flat-prose ruleset already lives at the repo root.

That's it. From the next change on, the docs answer to the code.

## Commands

Three axes — **truth · craft · hygiene** — one creed: prove it or drop it, you keep the final call.

| Command | What it does |
|---------|--------------|
| `/evergreen [off \| light \| strict]` | Set the intensity for this repo. No argument reports the current one. |
| `/evergreen:winnow [base-ref] [--prove-by-test]` | **Truth, deep.** Walk every claim that changed since a ref and *certify it true or surface it* — silence means certified, not just "no lie found." Always strict. With `--prove-by-test`, behavioral claims that reading can't settle are settled by execution (write the test the doc implies, run it): fails → drift proven, passes → certified by test. |
| `/evergreen:flourish <file> [--all] [--manual]` | **Craft.** Rewrite an accurate-but-ugly doc to a gold standard (mined from 28 top READMEs), then prove every claim against the code. Emits a diff — never a silent overwrite. The only sanctioned prose-rewrite. |
| `/evergreen:cultivate [path]` | **Hygiene.** Local-only files leaking into git, gitignore gaps, AI-slop that shouldn't be tracked or public. Proposes untrack/ignore/delete — never auto. A commit-time guard backstops it (the one thing that *blocks*). |
| `bin/evergreen impact [--repo PATH] [--evidence FILE] [--json] PATH...` | **Truth, candidate query.** Rank documentation related to changed paths and optional provider evidence. Read-only; never emits findings or verdicts. |

## FAQ

**Will it rewrite my prose?**
Not unless you ask. The reflex points; you write — a dead flag or moved path it hands you a diff for, the *why* behind a design it won't touch. The one exception is `/evergreen:flourish`, invoked deliberately: it crafts a doc to the gold standard, then verifies its own rewrite against the code so it can't introduce a lie. Fact-checker by default; ghostwriter only on request — and one that cites its sources.

**Won't it cry wolf?**
It flags only what it can prove against the code. Git's flags, CSS variables, other repos' paths, your ADRs — not its business. Tell it to drop something once and it offers the `.evergreen-ignore` line that keeps it dropped in every session after.

**Does it scale?**
It reads paths, contracts, and prose — not your AST. Any language, any repo, nothing to compile.

**Why "evergreen"?**
A doc that stays true as the code grows is evergreen. Yours aren't. Yet.

## Credits

Distilled from a survey of 309 repos — an idea mine, not a blueprint. The taxonomies and instincts behind the skill are credited to their sources in [`docs/DESIGN.md`](docs/DESIGN.md).

## License

[MIT](LICENSE). Keep the docs honest; do what you like with the code.
