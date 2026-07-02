<h1 align="center">🌲 Evergreen</h1>

<p align="center">
  <em>The docs said yes. The code said no. Only one of them gets to be true.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/a%20skill-not%20a%20scanner-111111?style=flat-square" alt="A skill, not a scanner">
  <img src="https://img.shields.io/badge/works%20in-any%20language-111111?style=flat-square" alt="Any language">
  <img src="https://img.shields.io/badge/eval-10%2F10%20recall%20%C2%B7%200%20FP-111111?style=flat-square" alt="10/10 recall, 0 false positives on the eval">
  <img src="https://img.shields.io/badge/license-MIT-111111?style=flat-square" alt="MIT license">
</p>

<p align="center">
  <strong>Cites the line or says nothing &middot; rewrites nothing unasked &middot; any language</strong><br>
  <sub>A documentation-freshness reflex for AI coding agents.</sub>
</p>

---

Your README was true the day you wrote it. Then a flag got renamed, a file moved, a function started returning something else — and the docs stayed exactly where they were. That's how documentation lies: not by being wrong when written, by being *left behind*. The gap opens quietly and nobody sees it until someone pastes a command that no longer exists.

Evergreen is the reflex that closes the gap. The moment your agent touches code, it reads the affected docs back against the source and surfaces only what it can prove has gone false — pointing at the exact line. It rewrites nothing on its own. It just refuses to let the docs and the code disagree in silence.

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

One rule above all: **prove it or drop it.** If it can't cite the code that makes the doc wrong, it isn't a finding. A checker that cries wolf gets muted — so this one never does.

## Receipts

That rule applies to evergreen itself. The [eval](eval/) seeds a fixture repo with 10 catalogued lies (two-plus per rung), 8 true claims that must not be flagged, and 2 exempt docs, then lets a headless agent winnow it blind:

| judge | drift caught | decoy false positives | exempt docs honored | precision |
|---|---|---|---|---|
| Opus 4.8 | 10/10 | 0/8 | 2/2 | 1.00 |
| Haiku 4.5 | 9/10 | 1/8 | 2/2 | 0.89 |

There's a second, per-pair [benchmark](eval/bench/) in the schema the research literature uses, reported at a **natural 10/90 class split** (drift is rare in the wild; balanced sets flatter precision by the prevalence gap — CASCADE's own precision drops 0.88 → 0.39 moving balanced → natural). On **CASCADE's released, execution-validated dataset** (885 wild Java pairs, arXiv:2604.19400), evergreen scores **F1 0.32 at 10/90 (Opus 4.8) vs the Cascade tool's 0.28** — the best F1 on the table, though Cascade keeps the precision crown (0.39 vs 0.30) — and on a **332-pair label-validated wild Python set** derived from CoDocBench it catches all validated drift (Haiku; 0.23 precision). Honest numbers on wild data, next to a published tool. The old headline 1.00/1.00 was a balanced sanity fixture (n=12, author-written) and is now labeled as exactly that. Numbers, method, caveats: [eval/bench/RESULTS.md](eval/bench/RESULTS.md) · [eval/RESULTS.md](eval/RESULTS.md). Re-run either: `bash eval/run.sh` · `python3 eval/bench/run_bench.py`.

## Install

### Claude Code

```
/plugin marketplace add ChrisPachulski/evergreen
/plugin install evergreen@evergreen
```

It rides along every session: flags drift the moment a change leaves a doc lying, adds `/evergreen:winnow`, and leaves a quiet nudge if you changed code and forgot to look. Intensity is `off | light | strict` (default **light**). The truth reflex never blocks your commit — it flags, you decide. (The hygiene guard is the one exception, and it's the kind you want — see [Commands](#commands).)

What it costs, since you count tokens: session start injects a ~35-line [digest](skills/evergreen/DIGEST.md), not the full ruleset (that loads on demand), and the post-turn nudge fires once per new change — not on every turn while the tree sits dirty.

### On every pull request

Want the check in CI too? Add the Action — it winnows the docs the PR's code touched and posts findings as a single comment. It never fails the build; it comments, you decide.

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
```

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
