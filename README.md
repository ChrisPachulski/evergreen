<h1 align="center">🌲 Evergreen</h1>

<p align="center">
  <em>Your README stopped lying.</em>
</p>

<p align="center">
  <a href="https://github.com/ChrisPachulski/evergreen/actions/workflows/ci.yml"><img src="https://github.com/ChrisPachulski/evergreen/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/drift_detection-any_language-2f855a?style=flat-square" alt="Drift detection: any language">
  <img src="https://img.shields.io/badge/doc_coverage-py_js_ts_go_rust-2f855a?style=flat-square" alt="Coverage parsers: py js ts go rust">
  <img src="https://img.shields.io/badge/runtime-git_+_grep_+_awk-111111?style=flat-square" alt="Runtime: git, grep, awk">
  <img src="https://img.shields.io/badge/license-MIT-111111?style=flat-square" alt="MIT license">
</p>

<p align="center">
  <strong>6 deterministic signals &middot; any language &middot; 0 tokens to run</strong><br>
  <sub>The documentation-freshness companion to <a href="https://github.com/DietrichGebert/ponytail">ponytail</a> — it answers <em>"is this doc still true?"</em> with grep and git before it ever asks a model.</sub>
</p>

---

You got paged at 3am because the README said one thing and the code did another. The setup steps pointed at a file that moved. The flag in the docs was renamed two sprints ago. The "quickstart" hasn't run since Q1. Nobody lied on purpose — the code just walked off and the docs stayed put.

Ponytail asks *"does this code need to exist?"*. **Evergreen asks "does this doc still match the code?"** — and it proves the answer against the code before it spends a single token.

## Before / after

Your README's setup section sends people to `app/server.py`. You moved it to `services/api/main.py` two commits ago. The doc still points at the ghost; the next person to clone burns an hour.

With evergreen, the moment the doc and the code disagree:

```text
$ evergreen-scan
evergreen: 1 finding(s)
  [high] in_docs_not_code   README.md — names `app/server.py` which does not exist on disk
```

No model. No config. Just grep and git, run in a fraction of a second. Then fix it by hand, or let `--fix` / `--fix-prose` do the derivable part.

## How it works

Find the drift at the first rung that holds — cheapest first, and never spend a model on what grep already knows:

```
1. Did a doc-named path vanish?          → grep + git          (zero tokens)
2. Does a documented flag / env exist?   → whole-token match   (zero tokens)
3. Did a pinned snippet drift?           → embed-from-source + SHA manifest
4. Does a tagged example still run?      → execute it (opt-in)
5. Only then — semantic drift            → a model, to CLASSIFY, never to detect
```

Rungs 1–4 are the deterministic engine (`bin/evergreen-scan`): zero LLM, zero token cost, no false-negatives. A model is the last resort, not the first reflex.

## It is not a two-language tool

Drift detection reads **paths, flags, and env keys — not your AST** — so rungs 1–4 work on **any language, any repo**. The only language-specific feature is *doc-comment coverage*, and that is parser-backed in five languages and regex everywhere else:

| Layer | Languages | How |
|---|---|---|
| **Drift signals (1–4)** | **any** | git + grep, language-agnostic |
| Doc coverage — Python | ✓ | stdlib `ast` |
| Doc coverage — JS / TS | ✓ | `deno doc --json` |
| Doc coverage — Go | ✓ | stdlib `go/ast` |
| Doc coverage — Rust | ✓ | `syn` |
| Doc coverage — anything else | ✓ | regex heuristic |

Every coverage parser is a single-file syntactic parse (no import resolution), built and cached on first use, with a graceful regex fallback when the toolchain is absent.

## What it checks

The deterministic signals, pinned to the engine source — **this block is written by `evergreen-scan --fix`, not by hand** (see [Keep it fresh](#keep-it-fresh)):

<!-- evergreen:embed bin/evergreen-scan:6-11 -->
```text
#   1. doc-named path existence : a path a doc names that no longer exists on disk  -> in_docs_not_code
#   2. rename cross-reference   : a doc still cites a path git just renamed/deleted -> in_docs_not_code
#   3. contract existence       : a CLI flag/env var a doc documents but no code has -> in_docs_not_code
#   4. embed-from-source        : a fenced block pinned to source lines that drifted -> in_docs_not_code
#   5. SHA-pinned manifest      : a doc pinned to a source whose hash since changed  -> needs_reverify
#   6. runnable example (opt-in): a tagged fenced block that exits nonzero           -> in_docs_not_code
```

## Install

### Claude Code (plugin)

```
/plugin marketplace add ChrisPachulski/evergreen
/plugin install evergreen@evergreen
```

Then it rides along: it flags drift when your code changes leave a doc lying, adds the `/evergreen:audit` command, and runs a non-blocking Stop-hook nudge. Strictness is `off | warn | block` (default **warn** — flag, never block the commit).

### Any repo (standalone CLI)

No Claude required — it is one portable bash script (`bin/evergreen-scan`, needs only `git`, `grep`, `awk`):

```sh
git clone https://github.com/ChrisPachulski/evergreen
ln -s "$PWD/evergreen/bin/evergreen-scan" ~/.local/bin/evergreen-scan
cd ~/your/repo && evergreen-scan
```

## Commands

| Command | What it does |
|---|---|
| `evergreen-scan` | Scan for drift. Exits 0 clean. |
| `evergreen-scan --json` / `--sarif` | Machine output (`freshness_pct`) / SARIF 2.1.0 for GitHub code-scanning. |
| `evergreen-scan --ci --fail-level high` | Exit 2 on drift at/above a severity — for CI and pre-commit. |
| `evergreen-scan --coverage [--fail-under N] [--badge]` | Doc-comment coverage, with a CI gate and a shields.io badge. |
| `evergreen-scan --fix` | Apply the *derivable* fixes only (re-embed from source, re-pin a hash). Never prose. |
| `evergreen-scan --fix-prose` | Opt-in LLM fix for dead references, behind three gates (needs the `claude` CLI). |
| `evergreen-scan --run-examples` | Also execute fenced examples tagged `evergreen` (off by default — it runs code). |
| `evergreen-scan --selftest` | Built-in self-check. |
| `/evergreen:audit [base-ref]` | Full freshness audit (Claude Code plugin). |

Per-repo tuning lives in `.evergreen.sh`: `CODE_ROOTS`, `EXEMPT`, `EXTERNAL_TOOLS`, and the `IGNORE_DOCS` / `IGNORE_FLAGS` / `IGNORE_ENV` noise valves.

## Keep it fresh

Two opt-in bindings make a doc *structurally unable* to drift, both deterministic and in the binary:

- **Embed-from-source** — mark a fenced block with `<!-- evergreen:embed path/to/src:10-20 -->`; the block is checked against those source lines and `--fix` rewrites it from source. (This README's "What it checks" block is one.)
- **SHA-pinned manifest** — a `.evergreen-manifest` TSV line binds a doc to a source file (or a line range); when the pinned content changes the doc is flagged `needs_reverify` and `--fix` re-pins it.

`--fix` only ever touches the fully derivable bits. `--fix-prose` (opt-in) corrects dead references — a file path, CLI flag, or env key the code no longer has — and gates the draft three ways (removes every stale token, adds no net lines, changes only stale-bearing lines) plus an independent review-call, then writes to the working tree and prints the diff. It never commits, and a changed signature or free-form rationale stays flagged for a human.

## FAQ

**Does it need a model?**
Not to find drift. Rungs 1–4 are grep and git — a clean repo costs zero tokens. A model only ever *classifies* what the deterministic pass already caught; it never detects.

**Won't it cry wolf?**
It flags only what it can prove against the code. CSS custom properties, another tool's flags (`git …`, `docker …`), and trailing-dash fragments are dropped automatically; design specs and dated/audit snapshots are exempt by default; the rest you silence once in `.evergreen.sh` and it never returns.

**Only two languages?**
No — that is the first thing people assume and it is wrong. Drift detection is language-agnostic. Doc-comment coverage is parser-backed in five (Python, JS, TS, Go, Rust) and regex in the rest. See [the table](#it-is-not-a-two-language-tool).

**Does evergreen use evergreen?**
Yes. This README's "What it checks" block is written by `evergreen-scan --fix` from the engine source, and CI fails the build if any doc drifts. It eats its own dogfood.

## Credits

Synthesized from a survey of **309 GitHub repos** (164 directly related, 79 of them zero-star). Techniques are credited to their source repos in [`docs/DESIGN.md`](docs/DESIGN.md) and the `.research/` mining notes — kedge, docs-drift-check, interrogate, Jan-ARN/drift, doc-checks, axiom-graph, docfresh, and many more. Built to pair with [ponytail](https://github.com/DietrichGebert/ponytail).

## License

[MIT](LICENSE). Keep the docs honest; do what you like with the code.
