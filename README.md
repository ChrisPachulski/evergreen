<h1 align="center">🌲 Evergreen</h1>

<p align="center">
  <em>The docs said yes. The code said no. She believed the code.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/a%20skill-not%20a%20scanner-111111?style=flat-square" alt="A skill, not a scanner">
  <img src="https://img.shields.io/badge/works%20in-any%20language-111111?style=flat-square" alt="Any language">
  <img src="https://img.shields.io/badge/companion%20to-ponytail-111111?style=flat-square" alt="Companion to ponytail">
  <img src="https://img.shields.io/badge/license-MIT-111111?style=flat-square" alt="MIT license">
</p>

<p align="center">
  <strong>Cites the line or says nothing &middot; rewrites nothing unasked &middot; any language</strong><br>
  <sub>The documentation-freshness companion to <a href="https://github.com/DietrichGebert/ponytail">ponytail</a>.</sub>
</p>

---

You know her. Reads the README with the source open in the other window, and trusts the source. You tell her the docs cover it; she's already on line 42, where the flag you documented stopped existing two sprints ago. She doesn't argue. She points.

Evergreen puts her inside your AI agent.

## Before / after

You rename a flag and move on. Three files still document the old name. Nobody notices until someone copies a broken command.

With evergreen, in the same turn:

```
evergreen: you renamed --workers to --concurrency.
  README.md:42   documents --workers — gone from cli.py → fix
  docs/cli.md:8  same flag, same fix
left alone: docs/adr/0003.md mentions --workers — an ADR, frozen in time.
```

She cites the line or she says nothing. And she leaves the docs that are *meant* to describe the past — ADRs, specs, dated snapshots — alone. They lead the code; they don't lie about it.

More of what she catches, one per rung, in [examples/](examples/).

## How it works

When code changes, she stops at the first rung that catches:

```
1. A doc names a file that's gone?      → grep, confirm, flag
2. A documented flag / env / route gone? → grep the code, flag
3. A shown snippet drifted from source? → read both, compare
4. Does the prose still tell the truth? → only then, reason
```

One rule above all: **prove it or drop it.** If she can't cite the code that makes the doc wrong, it isn't a finding. A checker that cries wolf gets muted — she knows.

## Install

### Claude Code

```
/plugin marketplace add ChrisPachulski/evergreen
/plugin install evergreen@evergreen
```

She rides along every session: flags drift the moment a change leaves a doc lying, adds `/evergreen:winnow`, and leaves a quiet nudge if you changed code and forgot to look. Intensity is `off | light | strict` (default **light**). The truth reflex never blocks your commit — it flags, you decide. (The hygiene guard is the one exception, and it's the kind you want — see [Commands](#commands).)

### Any other agent

The whole of her is [`skills/evergreen/SKILL.md`](skills/evergreen/SKILL.md). Drop it into any skill-capable agent, or paste it into your system prompt, and she comes with it. For Codex, Copilot, Gemini, and anything that reads [`AGENTS.md`](AGENTS.md), the flat-prose ruleset already lives at the repo root.

That's it. She's already reading your README. The code's open in the other window.

## Commands

Three axes — **truth · craft · hygiene** — one creed: prove it or drop it, you keep the final call.

| Command | What it does |
|---------|--------------|
| `/evergreen [off \| light \| strict]` | Set the intensity for this repo. No argument reports the current one. |
| `/evergreen:winnow [base-ref]` | **Truth, deep.** Walk every claim that changed since a ref and *certify it true or surface it* — silence means certified, not just "no lie found." Always strict. |
| `/evergreen:flourish <file> [--all] [--manual]` | **Craft.** Rewrite an accurate-but-ugly doc to a gold standard (mined from 28 top READMEs), then prove every claim against the code. Emits a diff — never a silent overwrite. The only sanctioned prose-rewrite. |
| `/evergreen:cultivate [path]` | **Hygiene.** Local-only files leaking into git, gitignore gaps, AI-slop that shouldn't be tracked or public. Proposes untrack/ignore/delete — never auto. A commit-time guard backstops it (the one thing that *blocks*). |

## FAQ

**Will it rewrite my prose?**
Not unless you ask. The reflex points; you write — a dead flag or moved path it hands you a diff for, the *why* behind a design it won't touch. The one exception is `/evergreen:flourish`, invoked deliberately: it crafts a doc to the gold standard, then verifies its own rewrite against the code so it can't introduce a lie. Fact-checker by default; ghostwriter only on request — and one that cites its sources.

**Won't it cry wolf?**
She flags only what she can prove against the code. Git's flags, CSS variables, other repos' paths, your ADRs — not her business. Tell her to drop something once and it stays dropped.

**Does it scale?**
She reads paths, contracts, and prose — not your AST. Any language, any repo, nothing to compile.

**Why "evergreen"?**
A doc that stays true as the code grows is evergreen. Yours aren't. Yet.

## Credits

Distilled from a survey of 309 repos — an idea mine, not a blueprint. The taxonomies and instincts behind the skill are credited to their sources in [`docs/DESIGN.md`](docs/DESIGN.md). Built to pair with [ponytail](https://github.com/DietrichGebert/ponytail).

## License

[MIT](LICENSE). Keep the docs honest; do what you like with the code.
