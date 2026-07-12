---
description: Find additive documentation candidates before editing changed paths.
argument-hint: "[--repo PATH] [--evidence FILE] PATH..."
disable-model-invocation: true
allowed-tools: Bash(python3:*)
---

Run the plugin's candidate query and preserve its contract:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/bin/evergreen" impact --json $ARGUMENTS
```

Present `candidates` as pre-edit documentation candidates, ordered exactly as returned. Present
`warnings` separately. Candidates are not findings or verdicts. Do not edit the project.
