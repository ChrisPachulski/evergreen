# flourish report — logloom README

claims: 14 — certified: 11, cut: 2, markered: 1

## Conservation ledger

| section | disposition | now lives |
| --- | --- | --- |
| How the pieces fit together | demoted | `<details>` in the result |
| Parsing internals | demoted | `<details>` in the result |
| Changelog | cut | git history, `git log --oneline` |
| Benchmarks | cut | `docs/benchmarks.md` |

## The rewrite

```diff
--- README.md
+++ README.md
-# logloom
-A log reader.
+<h1 align="center">logloom</h1>
+
+<p align="center"><em>Reads your logs, counts the damage, says nothing else.</em></p>
```

## Verification

```sh
python -m pytest
```

14 passed, 0 failed.

## Second-provider judge

judge: codex ok — docs 1, certified 1, drift 0, unverified 0
