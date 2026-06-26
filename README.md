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
  <strong>Cites the line or says nothing &middot; never rewrites your prose &middot; any language</strong><br>
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

She rides along every session: flags drift the moment a change leaves a doc lying, adds `/evergreen:audit`, and leaves a quiet nudge if you changed code and forgot to look. Intensity is `off | light | strict` (default **light**). She never blocks your commit — she flags, you decide.

### Any other agent

The whole of her is [`skills/evergreen/SKILL.md`](skills/evergreen/SKILL.md). Drop it into any skill-capable agent, or paste it into your system prompt, and she comes with it.

That's it. She's already reading your README. The code's open in the other window.

## Commands

| Command | What it does |
|---------|--------------|
| `/evergreen [off \| light \| strict]` | Set the intensity for this repo. No argument reports the current one. |
| `/evergreen:audit [base-ref]` | One-off full pass over everything that changed since a ref. |

## FAQ

**Will it rewrite my prose?**
No. It points; you write. A dead flag or a moved path it'll hand you the diff for — the *why* behind a design it won't touch. She's a fact-checker, not your ghostwriter.

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
