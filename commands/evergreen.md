---
description: Set the evergreen freshness-reflex intensity for this repo (off | light | strict).
---
Set evergreen to ${1:-light}.

This records the evergreen mode for this repository:
- **off** — pause the reflex (same as saying "stop evergreen").
- **light** (default) — ladder rungs 1–3 (paths, contracts, snippets) + cite-only prose checks.
- **strict** — also run the full rung-4 semantic prose pass.

The new mode takes effect from your next message onward. To run a one-off full audit regardless of
mode, use `/evergreen:audit` (always strict).
