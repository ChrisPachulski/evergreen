# Task

Run `/evergreen:seed` against **only** `eval/fixture/`. Judge only code and docs
inside that directory, and do not modify anything. You are running from the
repository root, not from the fixture: do not inspect or classify Evergreen's
own docs, commands, skills, or source files.

The provider inventory below is pass-B output. It was produced before this run;
do not shell out or rerun the provider. Quote it in your report exactly as
given, including any warnings. Use only Read, Grep, and Glob.

Triaging must cover the supplied inventory. Follow the seed command's evidence
rules, including alias-aware documentation checks. Proposed documentation is
report-only: do not create files. For every proposed doc, include its complete
content in the required output and ledger each declarative claim against code.

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
```

One `candidate` row per inventory entry, in provider rank order with no rank skipped; the
`winnow` row's `certified` equals the number of `claim` rows and its other two counts are zero.
