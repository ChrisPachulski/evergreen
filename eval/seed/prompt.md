# Task

This file is the tail of the exam prompt. `eval/seed/run.sh` assembles the whole prompt in this
order: the evergreen skill body, `commands/seed.md`, the pass-B provider inventory under the
heading "Pass-B provider inventory (quote verbatim)", this task, and a "Judge entry point" section
naming the one shell command the run may use.

Run `/evergreen:seed` (the command in `commands/seed.md`) against **only** `eval/fixture/`.

## Declared deviations from the command

The exam narrows the command on purpose, and where the two differ the narrowing wins:

- **Scope.** Judge only code and docs inside `eval/fixture/`. Evergreen's own docs, commands,
  skills, and source files are the ruleset you run, never the subject.
- **Pass B.** The provider inventory in the prompt was produced before this run in place of the
  `till` call the command makes. Do not shell out or rerun the provider; quote it exactly as given,
  including any warnings.
- **Pass E writes.** The command writes approved docs as new files. This run writes nothing: each
  proposed doc is emitted whole as a `doc` row in the ledger below, which is what the scorer reads.
  Do not create or modify any file.
- **Tools.** The grant is Read, Grep, Glob, and the judge entry point; there is no write tool,
  which is why the runner saved the inventory file for you. The command's `git grep` becomes Grep;
  its evidence rules, including alias-aware documentation checks, still apply.

Triaging must cover the supplied inventory. For every proposed doc, include its complete content in
the required output and ledger each declarative claim against code.

Before any doc content, show the full worthy list with a plain recommendation of which to write
first and why, then the owner's chosen count. If the task carries `--approve N`, that is the owner's
count and you write the top N worthy in rank order; otherwise, with no owner answer, write nothing.
Certify every proposed doc with the winnow ladder before proposing it and report the counts.

End your reply with a fenced `jsonl` block and nothing else inside that block.
Emit one object per line, in this order:

```jsonl
{"type":"inventory","source_files":<n>,"scanned_files":<n>,"candidates":<n>,"warnings":["<each provider warning, verbatim>"]}
{"type":"candidate","symbol":"<name>","kind":"<kind>","code_ref":"<declaration file:line>","rank":<n>,"verdict":"documented|worthy|not-worthy|deferred","reader_use":"<one line>"}
{"type":"recommendation","worthy":["<symbol>", "..."],"recommend":<n>}
{"type":"owner","chosen":<n>}
{"type":"claim","doc":"<path>","claim":"<the sentence>","code_ref":"<file:line>","kind":"reader-use|fact"}
{"type":"gap","doc":"<path>","text":"<the marker>"}
{"type":"winnow","certified":<n>,"drift":<n>,"unverified":<n>}
{"type":"coverage","N":<n>,"documented":<n>,"worthy":<n>,"not_worthy":<n>,"deferred":<n>,"written":<n>}
{"type":"doc","path":"<path>","symbol":"<name>","lines":<n>,"body":"<full proposed content>"}
{"type":"judge","stage":"inventory","status":"ok","provider":"codex","docs":<n>,"certified":<n>,"drift":<n>,"unverified":<n>}
{"type":"judge","stage":"docs","status":"ok","provider":"codex","docs":<n>,"certified":<n>,"drift":<n>,"unverified":<n>}
```

One `candidate` row per inventory entry, in provider rank order with no rank skipped; the
`winnow` row's `certified` equals the number of `claim` rows and its other two counts are zero.
Judge the inventory before triage: the runner saved the supplied pass-B inventory as `till.json`
in the sandbox root (the "Judge entry point" section names it); run the judge entry point with
`--repo . --inventory till.json --json` and emit its summary as the `stage: inventory` judge row on
every run. If any doc was written, run the entry point with
`--repo . --doc <doc> --json` for each proposed doc and sum the results into the `stage: docs`
judge row — omit that row only when no doc was written. An abstention (codex unavailable, or the
call refused) is reported as `{"type":"judge","status":"abstain","provider":"codex","docs":<n
judged so far>,"certified":<n>,"drift":<n>,"unverified":<n>,"reason":"<the abstention reason>"}`,
never omitted.
