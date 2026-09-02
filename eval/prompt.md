# Task

The ruleset above is in force. Run the deep affirmative pass (winnow) on the repository in the
current directory: walk **every** documented claim in `README.md`, `docs/`, and any other doc
file here, and leave each one certified (doc and current code match), a finding (cite the code
that makes it wrong), or exempt/unverified per the ruleset. Judge only this directory's docs
against this directory's code. Do not modify any file.

Enumerate the claim set first: count every documented claim in scope with one countable command
(a grep over the claim-bearing lines of the docs in scope), state `N` and that command, then give
each of the N claims its own verdict. Certified rows cite the code `path:line` that makes the claim
true; drift rows cite the code that makes it wrong; unverified rows say why the code cannot settle
it. The three counts must sum to N.

When the pass is complete, end your reply with a fenced block tagged `jsonl` containing one JSON
object per line and nothing else:

- Once, the census:
  `{"type": "claims", "n": <N>, "command": "<the command that counted N>"}`
- For each claim, exactly one verdict row:
  `{"type": "verdict", "status": "certified", "file": "<doc path>", "line": <line>, "claim": "<the doc phrase>", "code_ref": "<code file:line that makes it true>"}`
  `{"type": "verdict", "status": "drift", "file": "<doc path>", "line": <line>, "claim": "<the exact doc phrase that is wrong>", "code_ref": "<code file:line>", "why": "<one line, citing code file:line>"}`
  `{"type": "verdict", "status": "unverified", "file": "<doc path>", "line": <line>, "claim": "<the doc phrase>", "why": "<what the code cannot settle>"}`
- For each doc you left alone as exempt:
  `{"type": "left_alone", "file": "<doc path>", "reason": "<why>"}`

Only emit a drift verdict you can prove against the code. The `claim` field must quote the doc's
own words, not the code's.
