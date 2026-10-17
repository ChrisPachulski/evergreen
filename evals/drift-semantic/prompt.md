---
max_turns: 50
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill]
---

Do a full, strict pass over README.md and docs/usage.md in this repo: walk every claim they make, including code snippets, default values, and prose about behavior, and prove each one against the code. Tell me exactly which claims the code contradicts, citing the file and line on both sides. Leave the changelog and ADRs alone; they are history.
