---
type: llm
weight: 2
---

The workspace is a small CLI whose docs contain planted lies AND true statements. Judge only the false-positive side.

PASS if the response does NOT report any of these as wrong, missing, or drifted: the `--retries` flag, the `SHIPIT_TOKEN` env var, the `config.py` path, the `--verbose` / `-v` flag, the `--concurrency` flag, "3 attempts", "defaults to table", or the `git push --follow-tags` advice. Mentioning them as verified, correct, or left alone is fine.
PASS also requires that the response does not propose editing CHANGELOG.md or docs/adr/0001-worker-pool.md; noting them as history or exempt is fine.
FAIL if any of those true claims is flagged as drift, or if an edit to the changelog or ADR is proposed.
