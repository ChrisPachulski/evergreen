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

End your reply with a fenced `jsonl` block and nothing else inside that block.
Emit one object per line:

```jsonl
{"type":"candidate","symbol":"<name>","rank":<n>,"verdict":"documented|worthy|not-worthy|deferred","reader_use":"<one line>"}
{"type":"claim","doc":"<path>","claim":"<the sentence>","code_ref":"<file:line>"}
{"type":"gap","doc":"<path>","text":"<the marker>"}
{"type":"coverage","N":<n>,"documented":<n>,"worthy":<n>,"not_worthy":<n>,"deferred":<n>,"written":<n>}
{"type":"doc","path":"<path>","lines":<n>,"body":"<full proposed content>"}
```
