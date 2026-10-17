---
description: "Set or report the evergreen freshness-reflex intensity for this repo (off | light | strict). Invoked as /evergreen:mode."
argument-hint: "[off | light | strict]"
---
/evergreen:mode $ARGUMENTS

The first line of this prompt is the mode sigil. The UserPromptSubmit tracker hook persists it
before you read this: when an intensity was given, `.evergreen-mode` at the project root already
holds it and the file is excluded from git through `.git/info/exclude`. It takes effect from the
next message. Confirm in one line, `evergreen: mode set to <mode>`, and do not restate the sigil.

- off -- pause the ride-along reflex. Commands you type still run at full depth.
- light (default) -- ladder rungs 1-3 (paths, contracts, snippets) only; never a prose finding.
- strict -- also the full rung-4 semantic prose pass.

If no argument was given: read `.evergreen-mode` (default light if absent) and report the current
mode in one line. Do not change it.

For a one-off full pass regardless of mode, use `/evergreen:winnow` (always strict).
