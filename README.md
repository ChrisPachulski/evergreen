<h1 align="center">🌲 Evergreen</h1>

<p align="center">
  <em>Your README stopped lying.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/type-skill,_not_a_scanner-2f855a?style=flat-square" alt="A skill, not a scanner">
  <img src="https://img.shields.io/badge/scope-any_language-2f855a?style=flat-square" alt="Any language">
  <img src="https://img.shields.io/badge/companion_to-ponytail-111111?style=flat-square" alt="Companion to ponytail">
  <img src="https://img.shields.io/badge/license-MIT-111111?style=flat-square" alt="MIT license">
</p>

<p align="center">
  <strong>A reflex, not a linter &middot; prove-or-drop &middot; any language</strong><br>
  <sub>The documentation-freshness companion to <a href="https://github.com/DietrichGebert/ponytail">ponytail</a>.</sub>
</p>

---

You got paged at 3am because the README said one thing and the code did another. The setup steps pointed at a file that moved. The flag in the docs was renamed two sprints ago. The "quickstart" hasn't run since Q1. Nobody lied on purpose — the code walked off and the docs stayed put.

Ponytail puts a lazy senior dev inside your agent, so it writes less code. **Evergreen puts a burned documentarian inside your agent, so it won't let the docs lie.** Same idea, the other half of the repo: ponytail is a skill that changes how the agent *writes*; evergreen is a skill that changes what the agent *notices* when the code and the docs drift apart.

No scanner. No CI service. No tokens spent on a clean repo. It's a prompt — a reflex the model runs with the tools it already has.

## Before / after

You rename a flag and move on. Three files still document the old name; nobody notices until a user copies a broken command.

With evergreen, in the same turn you made the change:

```
evergreen: you renamed --workers to --concurrency.
  [high] in_docs_not_code  README.md:42      documents `--workers`, now absent in cli.py → update or revert
  [high] in_docs_not_code  docs/cli.md:8     same flag, same fix
  [low]  exempt            docs/adr/0003.md  cites --workers — ADR, frozen in time, left alone
fresh once those two lines change.
```

It cites the code for every finding, and it leaves the docs that are *meant* to describe the past (ADRs, specs, dated snapshots) alone.

## How it works

When code changes, the agent walks a ladder and stops at the first rung that holds — the cheap, mechanical checks before the semantic one:

```
1. Did a doc-named path vanish?          → grep the repo, confirm the file
2. Does a documented contract exist?     → grep for the flag / env / function / route
3. Did a shown snippet or signature drift? → read both, compare
4. Does the prose still tell the truth?  → reason, with the code in front of you
```

Then one rule above all: **prove it or drop it.** If it can't cite the code that makes the doc wrong, it isn't a finding. A checker that cries wolf gets muted.

It is **language-agnostic** — it reads paths, contracts, and prose, not your AST, so it works in any repo. And it never auto-rewrites prose: derivable drift (a dead reference, an endpoint table, a snippet that should mirror its source) gets a proposed diff; a changed signature or the *why* behind a design gets flagged for a human.

## Install

### Claude Code

```
/plugin marketplace add ChrisPachulski/evergreen
/plugin install evergreen@evergreen
```

Then it rides along every turn that touches code-with-docs: it flags drift the moment your change leaves a doc lying, adds the `/evergreen:audit` command, and runs a non-blocking Stop-hook nudge. Strictness is `off | warn | block` (default **warn** — flag, never block the commit).

### Any other agent

Evergreen is a skill — the whole thing is [`skills/evergreen/SKILL.md`](skills/evergreen/SKILL.md). Drop that ruleset into any skill-capable agent (or paste it into your system prompt) and the reflex comes with it.

## Commands

| Command | What it does |
|---|---|
| `/evergreen [off \| warn \| block]` | Set strictness, or turn the reflex off. |
| `/evergreen:audit [base-ref]` | Full freshness pass over what changed since a ref. |

## FAQ

**Is it a scanner / linter?**
No. It's a skill — a prompt that makes the model check doc freshness with the tools it already has. There's no engine to install, nothing to run in CI, and a clean repo costs zero tokens because nothing fires.

**Won't it cry wolf?**
It flags only what it can prove against the code. Third-party tool flags (`git …`, `docker …`), CSS custom properties, and design specs / ADRs / dated snapshots are left alone by default; the rest you tell it to ignore once and it stops.

**Does it rewrite my docs?**
Only the derivable parts, and only as a proposed diff you apply — a dead reference, a table, a snippet that should mirror its source. Prose, rationale, and changed signatures it flags for a human and never touches.

**Why "evergreen"?**
A doc that stays true as the code grows is evergreen. Most aren't.

## Credits

Synthesized from a survey of **309 GitHub repos** (164 directly related, 79 of them zero-star) — an idea mine, not an infrastructure blueprint. The heuristics, taxonomies, and mental models behind the skill are credited to their source repos in [`docs/DESIGN.md`](docs/DESIGN.md): kedge, docs-drift-check, interrogate, Jan-ARN/drift, doc-checks, axiom-graph, docfresh, and many more. Built to pair with [ponytail](https://github.com/DietrichGebert/ponytail).

## License

[MIT](LICENSE). Keep the docs honest; do what you like with the code.
