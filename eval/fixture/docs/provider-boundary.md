# Provider boundary

This fixture separates mechanically observed facts from Evergreen's semantic judgment.

## Proven contract mismatch

Claim under test: `load_config()` returns `None` when `project` is absent.

Provider fact: the return contract changed to raising `KeyError`.

Expected: finding — but only after re-reading current `config.py` confirms the indexed
`cfg["project"]` access.

## Tempting semantic false positive

Claim under test: a project can override the timeout in its JSON configuration.

Provider fact: `DEFAULT_TIMEOUT` changed from 60 to 30.

Expected: no finding — the per-project timeout override remains true because `setdefault`
preserves an existing `timeout`. The mechanical fact nominates timeout-related documentation; it
does not prove that every timeout-related sentence is false.
