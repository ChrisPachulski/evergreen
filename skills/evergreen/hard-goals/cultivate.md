# Hard goals — `cultivate`

**Frozen contract.** A cultivate run that fails any goal below is *not done*, no matter how good its
prose reads. Pre-committed: the bar is fixed before the work.

Each goal passes the hardness test in [`winnow.md`](winnow.md): **binary · checkable without trusting
the AI · pre-committed · covers the hard part.** If a check needs the AI's opinion to pass, rewrite it.

## The goals

1. **MUST inventory the filesystem, not the index.**
   CHECK: the run shows both `git ls-files | wc -l` and `find . -type f -not -path './.git/*' | wc -l`,
   and the gap between them is accounted for line-by-line. Pass = both counts shown and the gap
   explained (every excess file named or dismissed with a reason).

2. **MUST run the reference graph for every committed non-entry-point file.**
   CHECK: each such file's verdict carries an executed `git grep` result (references found, or zero).
   Pass = no `keep` / `orphan` verdict without its grep behind it.

3. **MUST NOT output "clean" / "no slop" as a verdict.**
   CHECK: grep the output for `clean` / `no slop` used as a conclusion → absent; an evidenced
   inventory plus an explicit "did NOT check" list is present instead. Pass = no bare clean verdict.

4. **MUST check the repo's real exposure against `gh`, not prose.**
   CHECK: the run shows the output of `gh repo view --json visibility,isPrivate,nameWithOwner`
   (or explicitly flags visibility as *unchecked* when `gh` is unavailable). Pass = real `gh` output
   shown, or visibility flagged unchecked — never silently assumed.

5. **MUST classify every non-trivial code element as tested-with-a-real-assertion or a gap.**
   CHECK: the deserving set is enumerated from the code (the command is shown); enumerated count ==
   classified count, and each "tested" claim cites a test `file:line` whose body asserts. Pass = the
   counts match and no "tested" claim points at an assertion-free test.

6. **MUST state what was NOT checked.**
   CHECK: a coverage section exists and names specifics (paths, passes, or elements skipped).
   Pass = present and specific, not "checked everything".

## Why this works without a second AI at runtime

Every CHECK is a command or count a third party — or the same model on a later pass — re-runs to the
same yes/no. The frozen contract *is* the external arbiter. A run that reads thorough but fails goal 2
(a `keep` with no grep behind it) is a failed run, full stop.
