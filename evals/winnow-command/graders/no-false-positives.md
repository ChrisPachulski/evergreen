---
type: llm
weight: 2
---

The workspace is a small CLI whose docs contain planted lies AND true statements. Judge only the false-positive side.

PASS if the response does NOT claim that any of these is missing, renamed, or wrong: the `--retries` flag or its default of 3, the `SHIPIT_TOKEN` env var, the `config.py` path, the `--verbose` / `-v` flag, the `--concurrency` flag, "defaults to table", or the `git push --follow-tags` advice. Mentioning them as verified, correct, or left alone is fine.
Saying that `--retries` or `--concurrency` is parsed but never used by `main()` in cli.py, so the retry or parallelism prose is drift or unverified, is correct and does NOT fail.
PASS also requires that the response does not propose editing CHANGELOG.md or docs/adr/0001-worker-pool.md; noting them as history or exempt is fine.
FAIL if any of those flags, env vars, paths, or defaults is called dead, renamed, or wrong, or if an edit to the changelog or ADR is proposed.
