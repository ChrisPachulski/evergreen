# Task

Run `/evergreen:cultivate` against the repository that is your current working
directory. This is an isolated disposable fixture, not the Evergreen repository.
Use Bash, Read, Grep, and Glob as needed. The run is read-only: propose any
untrack, ignore, or deletion action, but never execute it.

Run every mandatory cultivate pass. In particular, inventory the filesystem and
the Git index, preserve the dynamic-import blind spot during any reference-graph
judgment, report visibility as unchecked when the no-remote `gh` call fails, and
do not call the repository clean or no slop. State exactly what you did not
check.

End your reply with a fenced `jsonl` block and nothing else inside that block.
Emit one object per line:

```jsonl
{"type":"verdict","path":"<p>","verdict":"keep|orphan|untrack|ignore|delete-proposed","evidence":"<the executed grep result or reason>"}
{"type":"inventory","tracked":<n>,"on_disk":<n>,"gap_accounted":<n>}
{"type":"exposure","gh_ran":true|false,"visibility":"<value or unchecked>"}
{"type":"tested","element":"<symbol>","status":"tested|gap","test_ref":"<file:line or null>"}
{"type":"not_checked","items":["<specific path or pass>", "..."]}
```
