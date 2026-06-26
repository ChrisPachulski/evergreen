<!-- Evergreen keeps a single source of truth to avoid the doc drift it exists to catch. -->
# Evergreen

The evergreen doc-freshness rules live in [`AGENTS.md`](../AGENTS.md) at the repo root — load and
follow them. In short: whenever you change code that has docs, walk the freshness ladder (vanished
paths → dead contracts → drifted snippets → semantic prose), prove every finding against the code,
propose diffs only for what's derivable, and flag (never rewrite) prose and the *why*.
