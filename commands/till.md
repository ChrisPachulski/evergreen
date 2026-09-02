---
description: Inventory the undocumented-surface candidates — every public declaration reachable from outside its file.
argument-hint: "[--repo PATH] [PATH...]"
disable-model-invocation: true
allowed-tools: Bash(python3:*)
---

Run the plugin's surface-inventory query and preserve its contract:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/bin/evergreen" till --json $ARGUMENTS
```

Present `candidates` (symbol, kind, path:line, rank) exactly as returned, then
`source_files_scanned` of `source_files_in_scope`, then `warnings` separately. Any warning
containing `truncated` means the inventory is incomplete — say so; a seed run must fail closed on
it. Any warning containing `outside inventory` counts tracked or untracked source the provider
deliberately excludes — repeat it verbatim; nothing in those files may be classified as documented
or not worthy. Candidates are nominations, never verdicts. Then double-check the inventory on a second provider:
save the JSON to a scratch path and run
`python3 "${CLAUDE_PLUGIN_ROOT}/bin/evergreen" judge --repo . --inventory <that file>`;
present every disagreement (a listed symbol the judge finds private or absent, a public declaration
it finds missing) and its trailer line verbatim, or `judge: abstain — <reason>`. Do not edit the
project.
