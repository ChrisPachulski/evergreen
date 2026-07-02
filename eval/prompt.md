# Task

The ruleset above is in force. Run the deep affirmative pass (winnow) on the repository in the
current directory: walk **every** documented claim in `README.md`, `docs/`, and any other doc
file here, and leave each one certified (doc and current code match), a finding (cite the code
that makes it wrong), or exempt/unverified per the ruleset. Judge only this directory's docs
against this directory's code. Do not modify any file.

When the pass is complete, end your reply with a fenced block tagged `jsonl` containing one JSON
object per line and nothing else:

- For each finding:
  `{"type": "flag", "file": "<doc path>", "line": <line number>, "claim": "<the exact doc phrase that is wrong>", "why": "<one line, citing code file:line>"}`
- For each doc you left alone as exempt:
  `{"type": "left_alone", "file": "<doc path>", "reason": "<why>"}`

Only emit a flag you can prove against the code. The `claim` field must quote the doc's own
words, not the code's.
