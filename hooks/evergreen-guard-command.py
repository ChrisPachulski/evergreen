#!/usr/bin/env python3
"""Classify decoded Bash tool input for the evergreen commit guard.

The threat boundary is recognizable Git intent in shlex-tokenized subcommand position — the
first non-option token after a `git` word — not arbitrary commands computed through variables,
the output of substitutions, aliases, or shell evaluation. A command written literally inside a
`$(...)` or backtick substitution runs, so its body is scanned like any other command. Words
inside quoted arguments (commit messages, pathspecs) are single tokens and never create intent.
Unparseable input (unbalanced quotes) degrades to coarse quote-stripped splitting, which may fail
closed. Beyond add/commit, a `git tag` creation whose refname begins `evergreen--v` is
classified for the release certification gate; tag deletion, listing, and verification stay
unclassified. A literal `cd` or `git -C` names further directories the staged-index inspection
must cover; a location the guard cannot follow (computed paths, GIT_DIR or GIT_INDEX_FILE,
`--git-dir`) is reported as unresolved, as is nesting too deep or too wide to read, and refused.
So is a command naming git with a substitution whose end a `case` pattern or comment obscures.

shlex knows nothing of shell grammar beyond quoting, so three constructs must be separated from
the argument list before a commit's options are read, or their words are misread as positional
pathspecs: newlines (which end a command), redirection operators with their targets, and heredoc
bodies.

A heredoc body is always lifted out of the introducing command's argument list, because a body is
never an argument. Whether it is also SCANNED for intent is a separate question, and the default is
yes: an unrecognized command may hand its body to anything, and an allowlist of shells could never
be trusted to be complete. Scanning is skipped only where the body is provably not shell code — a
`git commit` reading its own message, a script for a language that is not the shell, a payload
copied into a file — and never on a line that also reaches a shell, `eval`, or `source`. Ambiguity
resolves toward scanning more text: recursion can only add detected intent, never hide it.
"""

import itertools
import json
import os
import re
import shlex
import sys


def normalize_shell_word_joins(command: str) -> str:
    command = command.replace("\\\n", "")
    command = re.sub(r"\\(.)", r"\1", command, flags=re.DOTALL)
    return command.replace("'", "").replace('"', "")


CONTROL_TOKENS = {";", "&&", "||", "&", "|", "(", ")", "{", "}"}
# Git global options that consume the FOLLOWING token as their value when not written in
# `--option=value` form; the value must be skipped so it is never read as the subcommand.
GIT_VALUE_GLOBALS = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace",
    "--config-env", "--attr-source", "--super-prefix", "--list-cmds",
}
# `git tag` forms that never certify anything: deletion, listing, and verification pass
# through. For `git tag`, `-v` means verify, not verbose.
TAG_NONCREATION = {"-d", "--delete", "-l", "--list", "-v", "--verify"}
# Tag-creation options that consume the following token as their value when not written
# `--option=value`, so the value is never read as the refname or committish.
TAG_VALUE_OPTIONS = {"-m", "--message", "-F", "--file", "-u", "--local-user", "--cleanup", "--trailer"}
RELEASE_TAG_PREFIX = "evergreen--v"
# Subcommands that can WRITE THE INDEX, and therefore decide what a commit in the same call
# finalizes. `add` is only the most common of them: `git mv secrets.txt .env && git commit`,
# `git apply --cached p.patch && git commit`, and `git stash pop && git commit` each stage
# content the PreToolUse hook never gets to inspect, because the index it reads is the one
# from before the call. Any of these paired with a commit is compound for the same reason
# `add` is. Only `add` and `commit` classify on their own — see main().
STAGING_SUBCOMMANDS = {
    "add", "mv", "rm", "apply", "stash", "restore", "checkout", "reset",
    "update-index", "read-tree", "cherry-pick", "revert", "merge", "rebase", "am",
}
UNSAFE_LONG = {
    "--all", "--include", "--only", "--interactive", "--patch",
    "--pathspec-from-file", "--pathspec-file-nul",
}
VALUE_OPTIONS = {
    "-m", "--message", "-F", "--file", "--author", "--date",
    "-C", "--reuse-message", "-c", "--reedit-message", "--fixup",
    "--squash", "--cleanup", "--trailer", "--gpg-sign",
}
SAFE_LONG = {
    "--quiet", "--verbose", "--no-verify", "--allow-empty",
    "--allow-empty-message", "--amend", "--no-post-rewrite", "--signoff",
    "--no-gpg-sign", "--dry-run", "--status", "--no-status", "--short",
    "--branch", "--porcelain", "--long", "--null", "--no-ahead-behind",
    "--ahead-behind", "--edit", "--no-edit", "--reset-author",
    # Its mode is optional and only ever attached (`--untracked-files=no`, `-uno`), so the next
    # token is never its value: reading it as one swallowed a following `-a`.
    "--untracked-files",
}
SAFE_SHORT = set("qvnsSezu")
UNSAFE_SHORT = set("aiop")
# Basenames that execute a quoted command-string body via -c (or a short cluster containing c,
# e.g. -lc/-xc/-ec). Matching the basename catches path-qualified forms like /bin/bash.
SHELL_INTERPRETERS = {"sh", "bash", "zsh", "dash", "ksh"}
# A redirection operator and its target are shell plumbing, not arguments to the command. Both
# glued (`>out`, `2>&1`, `<<EOF`) and separated (`> out`) forms occur; `<<<` is a here-string.
REDIRECTION_RE = re.compile(r"^(?:\d+|&)?(?:<<-|<<<|<<|>>|>\||&>>|&>|<>|<|>)(.*)$")
# `<<WORD`, `<<'WORD'`, `<<"WORD"`, and the tab-stripping `<<-WORD`, matched at a known offset.
HEREDOC_RE = re.compile(r"<<-?[ \t]*(?:'([^']*)'|\"([^\"]*)\"|([A-Za-z_][A-Za-z0-9_]*))")
# Redirection operators that carry a shlex punctuation character inside them. shlex would split
# the command at that character, stranding every later argument in a segment with no `commit` in
# it, so `git commit 2>&1 -a` read as safe. Each is rewritten to an equivalent plain redirection
# before tokenizing; none of them can name a pathspec, so nothing is lost by flattening them. A
# duplication keeps its target word, whatever it is (`>&1`, `>&-`, `>&"$fd"`), as the target of the
# plain redirection it becomes.
AMPERSAND_REDIRECT_RE = re.compile(r"(?<![&|])&(>>?)")
FD_DUPLICATION_RE = re.compile(r"(\d*[<>])&")
FORCED_WRITE_RE = re.compile(r">\|")
# Process substitution puts a command inside an argument list. Its parentheses split the token
# stream, so the arguments after it cannot be scanned; a commit carrying one is refused instead.
PROCESS_SUBSTITUTION = ("<(", ">(")
# Commands whose stdin is a script in a language that is not the shell. Words in a body they read
# are never shell words, so they create no Git intent — the reasoning that makes a quoted `-m`
# message inert. Anything NOT listed here is scanned, so an unknown interpreter fails closed.
STDIN_INTERPRETERS = {
    "python", "python2", "python3", "node", "nodejs", "deno", "ruby", "perl", "php", "lua",
    "awk", "gawk", "mawk", "sed", "jq", "ed", "bc", "dc", "psql", "mysql", "sqlite3",
    "R", "Rscript", "osascript",
}
# Commands that copy stdin onward unchanged. Their body is a payload only when the line also names
# a destination file; a bare `cat <<EOF` writes to stdout, which anything may be reading, so it is
# left to the default and scanned.
STDIN_WRITERS = {"cat", "tee", "dd", "base64"}
# Words that run their argument or their stdin as shell code, whatever else the line looks like.
SOURCE_WORDS = {".", "source", "eval"}
# Words that run the command after them, so the word that decides the line is further right.
COMMAND_WRAPPERS = {"env", "command", "exec", "nohup", "nice", "stdbuf"}
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Every scan of a nested body (eval/-c string, heredoc, substitution) is one level deeper. Past this
# many levels, or this many bodies in all, the command is refused outright: an uncapped walk let
# 1050 nested `$(` exhaust the Python stack, and the crash fell through to a fallback that allowed
# the commit. The total keeps a wide command from outrunning the hook's timeout, which also allows.
MAX_NESTING = 10
MAX_SCANS = 200
# The staged-index inspection reads the repository the command will commit in. A literal `cd` or
# `git -C` is followed; these redirect the repository or index in ways the hook does not follow, so
# a commit carrying one is refused rather than checked against the wrong index.
GIT_LOCATION_ENV = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"}
GIT_LOCATION_OPTIONS = {"--git-dir", "--work-tree"}
DIRECTORY_CHANGERS = {"cd", "pushd", "popd"}
DECLARATION_WORDS = {"export", "declare", "typeset", "local", "readonly"}
# A substitution leaves this mark where it stood. Every keyword comparison strips it, so a word
# glued to a substitution still reads as itself, but a `cd` or `-C` operand that carries it was
# computed, and the literal prefix around it names a directory the command may never run in.
COMPUTED = "\x1f"
# The `)` closing a substitution is found by counting parentheses, which knows no shell grammar.
# A `case` pattern's `)` and a `)` inside a comment are indistinguishable from it to the count, so
# a body carrying either word is refused instead of being delimited by a guess.
AMBIGUOUS_WORD_RE = re.compile(r"#|case(?=[\s;&|()]|$)")
# Each directory change is its own fact, however often the same operand repeats: `cd child && cd
# child` lands in child/child, and folding the two into one set member hid the second change.
CHANGE_SERIAL = itertools.count()


class AmbiguousSubstitution(Exception):
    """A substitution body whose end a parenthesis count cannot place."""


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _heredoc_operators(line: str) -> "list[tuple[str, bool, bool]]":
    """(delimiter, strips_tabs, expands) for each real heredoc operator on one line.

    Walking the line beats scanning it with a regex because only a walk knows what is quoted: a
    `<<` inside `-m "shift << operator"` introduces nothing, and treating it as an operator is how
    a commit message came to look like a heredoc. `<<<` is a here-string and is stepped over. An
    unquoted `<<` is an operator even glued to the word before it (`file<<'EOF'`), as bash reads
    it. `expands` is True for an unquoted delimiter, whose body still runs `$(...)` and backticks.
    """
    operators = []
    quote = None
    index = 0
    while index < len(line):
        character = line[index]
        if quote is not None:
            quote = None if character == quote else quote
            index += 1
            continue
        if character in "'\"":
            quote = character
            index += 1
            continue
        if not line.startswith("<<", index):
            index += 1
            continue
        if line.startswith("<<<", index):
            index += 3
            continue
        match = HEREDOC_RE.match(line, index)
        if not match:
            index += 2
            continue
        delimiter = next(group for group in match.groups() if group is not None)
        operators.append((delimiter, line.startswith("<<-", index), match.group(3) is not None))
        index = match.end()
    return operators


def _feeds_a_shell(line: str) -> bool:
    """True when the line introducing a heredoc hands its body to a shell or to eval."""
    for token in shell_tokens(line):
        if token == "eval" or _basename(token) in SHELL_INTERPRETERS:
            return True
    return False


def _command_word(segment: "list[str]") -> "tuple[int, str] | None":
    """(index, token) of the word a segment actually runs, past assignments, options, wrappers."""
    for index, token in enumerate(segment):
        if token.startswith("-") and token != "-":
            continue
        if ASSIGNMENT_RE.match(token) or _basename(token) in COMMAND_WRAPPERS:
            continue
        return index, token
    return None


def _line_segments(line: str) -> "list[str]":
    """Split one line at unquoted control operators into the commands it runs.

    A heredoc belongs to ONE of those commands, and only that command's shape says whether the
    body is data. Judging the whole line instead let a harmless `git commit -m x; ` prefix vouch
    for a body belonging to something else entirely. `&` and `|` are skipped where they are part
    of a redirection (`&>`, `2>&1`, `>|`) rather than a separator.
    """
    segments: list[str] = []
    current: list[str] = []
    quote = None
    for index, character in enumerate(line):
        if quote is not None:
            current.append(character)
            if character == quote:
                quote = None
            continue
        if character in "'\"":
            quote = character
            current.append(character)
            continue
        joins_a_redirection = (current and current[-1] in "<>") or (
            character == "&" and line[index + 1:index + 2] == ">"
        )
        if character in ";|&" and not joins_a_redirection:
            segments.append("".join(current))
            current = []
            continue
        current.append(character)
    segments.append("".join(current))
    return segments


def _redirects_to_file(line: str) -> bool:
    """True when an unquoted `>` sends this line's output somewhere other than the terminal."""
    quote = None
    for character in line:
        if quote is not None:
            quote = None if character == quote else quote
        elif character in "'\"":
            quote = character
        elif character == ">":
            return True
    return False


def _body_is_data(segment: str, captured: bool = False) -> bool:
    """True when the heredoc body this command segment introduces cannot become shell commands.

    The default is False, and that direction is the whole point: a body scanned needlessly costs a
    false positive, while a body skipped wrongly is a hole, and an allowlist of shells could not
    know about `fish`, `sh<<EOF`, or `. /dev/stdin`. Only three shapes are provably inert — a
    `git commit` reading its message, a script for an interpreter that is not the shell, and a
    payload copied into a file or `captured` by a command substitution, whose output becomes words
    rather than commands — and none of them counts on a line that also reaches a shell, `eval`, or
    `source`, which is why those are checked first.
    """
    if _feeds_a_shell(segment):
        return False
    tokens = shell_tokens(segment)
    found = _command_word(tokens)
    if found is None:
        return False
    index, word = found
    if word in SOURCE_WORDS:
        return False
    basename = _basename(word)
    if basename == "git":
        return git_subcommand(tokens, index) == "commit"
    if basename in STDIN_INTERPRETERS:
        return True
    return basename in STDIN_WRITERS and (captured or _redirects_to_file(segment))


def _skip_heredoc_bodies(text: str, index: int, pending: "list[tuple[str, bool]]") -> int:
    """Index just past the pending heredoc bodies that start at index, or len(text) if one is open."""
    for delimiter, strip_tabs in pending:
        while index < len(text):
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            line = text[index:end]
            index = end + 1
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                break
        else:
            return len(text)
    return index


def _substitution_end(text: str, start: int) -> int:
    """Index of the `)` closing the `$(` body that begins at start, or len(text) if none does.

    Quotes, nested parentheses, and heredoc bodies inside the substitution are honoured, so a `)`
    in a message read through `"$(cat <<'EOF' ... EOF)"` does not end the body early. An unclosed
    body runs to end of input, which can only scan more text, never less. An unquoted word that
    opens a `case` or a comment raises AmbiguousSubstitution: its `)` may or may not end the body.
    """
    depth = 1
    quote = None
    pending: "list[tuple[str, bool]]" = []
    index = start
    while index < len(text):
        character = text[index]
        if quote == "'":
            quote = None if character == "'" else quote
        elif character == "\\":
            index += 1
        elif quote == '"':
            quote = None if character == '"' else quote
        elif character in "'\"":
            quote = character
        elif (index == start or text[index - 1] in " \t\n;&|()") and AMBIGUOUS_WORD_RE.match(
            text, index
        ):
            raise AmbiguousSubstitution
        elif character == "\n" and pending:
            index = _skip_heredoc_bodies(text, index + 1, pending)
            pending = []
            continue
        elif text.startswith("<<<", index):
            index += 2
        elif heredoc := HEREDOC_RE.match(text, index):
            delimiter = next(group for group in heredoc.groups() if group is not None)
            pending.append((delimiter, text.startswith("<<-", index)))
            index = heredoc.end()
            continue
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(text)


def _substitutions(text: str, honor_quotes: bool = True) -> "tuple[list[str], str]":
    """(bodies, rest): the `$(...)` and backtick substitutions in text, which the shell runs, and
    the text with each replaced by a quoted COMPUTED mark, as if its output were empty.

    Single quotes make both literal; double quotes do not, which is how `git commit -m "$(git add
    x)"` ran an add the guard never saw. A heredoc body with an unquoted delimiter expands both
    kinds whatever quotes it holds, so it is read with `honor_quotes=False`. An unclosed substitution
    runs to end of input, scanning more text, never less. Only the body is scanned: the command a
    substitution's OUTPUT might form is computed, and stays outside the threat boundary. Keyword
    checks strip the mark, so `` `git add x`git commit `` still reads as a commit, while a path
    operand carrying it is known to be computed.
    """
    bodies = []
    rest = []
    last = 0
    quote = None
    index = 0
    while index < len(text):
        character = text[index]
        if quote == "'":
            quote = None if character == "'" else quote
        elif character == "\\":
            index += 1
        elif character == "`":
            end = index + 1
            while end < len(text) and text[end] != "`":
                end += 2 if text[end] == "\\" else 1
            bodies.append(text[index + 1:end].replace("\\`", "`"))
            rest.append(text[last:index] + '"' + COMPUTED + '"')
            index = last = end + 1
            continue
        elif text.startswith("$(", index):
            end = _substitution_end(text, index + 2)
            bodies.append(text[index + 2:end])
            rest.append(text[last:index] + '"' + COMPUTED + '"')
            index = last = end + 1
            continue
        elif honor_quotes and character in "'\"" and quote in (None, character):
            quote = None if quote else character
        index += 1
    rest.append(text[last:])
    return bodies, "".join(rest)


def split_heredocs(command: str, captured: bool = False) -> "tuple[str, list[str], list[str]]":
    """Separate heredoc bodies from the command text that introduces them.

    Two invariants hold this together, and both were learned the hard way:

    Text is never hidden. A body is lifted out of the argument scan only when its delimiter is
    actually found. An unterminated or mis-detected heredoc leaves every line where it was, so the
    worst a wrong guess can do is scan too much. Consuming to end of input would have done the
    opposite — silently deleting a later `git add` or `git commit` from the text being classified.

    Bodies are scanned by default. A body is skipped only when `_body_is_data` can name why the
    command that reads it makes it inert, exactly as words inside `-m '...'` are inert. Anything
    else — a body piped onward, a command this file cannot identify — is still classified, so a
    shell nobody thought to list cannot become a blind spot. A body is judged by the command
    segment that owns the operator, never by the whole line, and any line reaching a shell has
    every one of its bodies scanned.

    A body is quoted by nothing, but an unquoted delimiter still expands the substitutions inside
    it, even in a commit message, so those are returned as a third list whatever the body is.
    """
    lines = command.split("\n")
    kept: list[str] = []
    scannable: list[str] = []
    expanded: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        line_reaches_a_shell = _feeds_a_shell(line)
        for segment in _line_segments(line):
            for delimiter, strip_tabs, expands in _heredoc_operators(segment):
                body: list[str] = []
                cursor = index
                terminated = False
                while cursor < len(lines):
                    candidate = lines[cursor]
                    cursor += 1
                    compared = candidate.lstrip("\t") if strip_tabs else candidate
                    if compared == delimiter:
                        terminated = True
                        break
                    body.append(candidate)
                if not terminated:
                    continue
                index = cursor
                text = "\n".join(body)
                if expands:
                    expanded.extend(_substitutions(text, honor_quotes=False)[0])
                if line_reaches_a_shell or not _body_is_data(segment, captured):
                    scannable.append(text)
    return "\n".join(kept), scannable, expanded


def prepare(command: str, captured: bool = False) -> "tuple[str, list[str], list[str]]":
    """Normalize joined lines, lift heredoc bodies and substitution bodies out, and make newlines
    separate commands. Returns (command text, heredoc bodies, substitution bodies)."""
    stripped, scannable, substitutions = split_heredocs(command.replace("\\\n", ""), captured)
    bodies, stripped = _substitutions(stripped)
    substitutions += bodies
    stripped = AMPERSAND_REDIRECT_RE.sub(r"\1", stripped)
    stripped = FD_DUPLICATION_RE.sub(r"\1", stripped)
    stripped = FORCED_WRITE_RE.sub(">", stripped)
    return stripped.replace("\n", " ; "), scannable, substitutions


def _shell_body_indices(tokens: "list[str]") -> "set[int]":
    """Indices of tokens that are command-string bodies of `eval` or an interpreter `-c` call.

    A token qualifies when walking left over option tokens (within the control segment) reaches a
    word whose basename is a shell interpreter, and some short-option cluster in between contains
    `c` — covering `bash -c`, `/bin/sh -c`, `bash -lc`, `sh -e -c`, and `bash --norc -c`.
    Over-matching here is conservative: recursion can only ADD detected intents, never hide them.
    """
    result = set()
    for index, token in enumerate(tokens):
        if index and tokens[index - 1] == "eval":
            result.add(index)
            continue
        if token.startswith("-") or token in CONTROL_TOKENS:
            continue
        back = index - 1
        saw_command_flag = False
        while back >= 0:
            previous = tokens[back]
            if previous in CONTROL_TOKENS:
                break
            if previous.startswith("-") and previous != "-":
                if not previous.startswith("--") and "c" in previous[1:]:
                    saw_command_flag = True
                back -= 1
                continue
            if saw_command_flag and _basename(previous) in SHELL_INTERPRETERS:
                result.add(index)
            break
    return result


def shell_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|(){}")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return normalize_shell_word_joins(command).split()


def _subcommand_index(segment: list[str], git_index: int) -> "int | None":
    """Index of the first non-option token after segment[git_index], skipping global options."""
    index = git_index + 1
    while index < len(segment):
        token = segment[index]
        if token == "--":
            return None
        if token.startswith("-") and token != "-":
            option, equals, _value = token.partition("=")
            index += 2 if option in GIT_VALUE_GLOBALS and not equals else 1
            continue
        return index
    return None


def git_subcommand(segment: list[str], git_index: int) -> "str | None":
    """First non-option token after segment[git_index] (a `git` word), skipping global options."""
    index = _subcommand_index(segment, git_index)
    return None if index is None else segment[index]


def _release_tag_intent(segment: list[str], tag_index: int) -> "str | None":
    """Classify one `git tag` argument list; only evergreen--v* creations carry intent.

    A creation whose first positional starts with `evergreen--v` is `release-tag` when it
    tags the checked-out HEAD (no explicit committish, or the literal `HEAD`), and
    `release-tag-elsewhere` when it names any other explicit committish — the certification
    gate verifies records against HEAD only, so tagging elsewhere is refused, not guessed at.
    Deletion, listing, and verification forms return None and pass through.
    """
    positionals: list[str] = []
    consume = False
    for token in segment[tag_index + 1:]:
        if consume:
            consume = False
            continue
        if token in TAG_NONCREATION:
            return None
        if token == "--":
            continue
        option, equals, _value = token.partition("=")
        if option in TAG_VALUE_OPTIONS:
            consume = not equals
            continue
        if token.startswith("-") and not token.startswith("--") and token != "-":
            # A short cluster (`-am`, `-sm`, `-aF`): a value letter ends it, carrying the rest of
            # the cluster as its value, or taking the next token when it is the last letter.
            for index, flag in enumerate(token[1:], start=2):
                if flag in "mFu":
                    consume = index == len(token)
                    break
            continue
        if token.startswith("-") and token != "-":
            continue
        positionals.append(token)
    if not positionals or not positionals[0].startswith(RELEASE_TAG_PREFIX):
        return None
    if positionals[1:] in ([], ["HEAD"]):
        return "release-tag"
    return "release-tag-elsewhere"


def _literal_path(word: str) -> bool:
    """True when a shell word names one directory without expansion the hook would have to guess."""
    expanding = "$`*?[\n" + COMPUTED
    return bool(word) and word != "-" and not any(character in word for character in expanding)


def _directory_change(arguments: "list[str]", word: str) -> "str | tuple[str, str, int]":
    """("cd", path, serial) for a `cd`/`pushd` to one literal directory, else "unresolved-target"."""
    operands = [
        argument for argument in arguments
        if not REDIRECTION_RE.match(argument) and not (argument.startswith("-") and argument != "-")
    ]
    if word == "popd" or len(operands) > 1 or (operands and not _literal_path(operands[0])):
        return "unresolved-target"
    return "cd", os.path.expanduser(operands[0] if operands else "~"), next(CHANGE_SERIAL)


def _git_location(options: "list[str]") -> set:
    """{("C", path)} for the literal `-C` chain in a git invocation's global options.

    `--git-dir`, `--work-tree`, and a `-C` the hook cannot read literally are "unresolved-target".
    """
    chain = ""
    index = 0
    while index < len(options):
        option, equals, _value = options[index].replace(COMPUTED, "").partition("=")
        if option in GIT_LOCATION_OPTIONS:
            return {"unresolved-target"}
        if option == "-C" and not equals:
            path = options[index + 1] if index + 1 < len(options) else ""
            if not _literal_path(path):
                return {"unresolved-target"}
            chain = os.path.join(chain, os.path.expanduser(path))
        index += 2 if option in GIT_VALUE_GLOBALS and not equals else 1
    return {("C", chain)} if chain else set()


def _location_facts(marked: "list[str]") -> set:
    """Directory changes and git-location environment assignments one command segment makes.

    The segment arrives with its COMPUTED marks, which only the path operands keep."""
    segment = [token.replace(COMPUTED, "") for token in marked]
    facts = set()
    found = _command_word(segment)
    assigning = segment if found is None or found[1] in DECLARATION_WORDS else segment[:found[0]]
    if any(
        ASSIGNMENT_RE.match(token) and token.split("=", 1)[0] in GIT_LOCATION_ENV
        for token in assigning
    ):
        facts.add("unresolved-target")
    # Like `git`, any `cd` word counts, not only the segment head (`if cd x; then`), so over-reading
    # can only add a directory to inspect.
    for index, word in enumerate(segment):
        if word in DIRECTORY_CHANGERS:
            facts.add(_directory_change(marked[index + 1:], word))
    return facts


def collect_intents(
    command: str, depth: int = 0, captured: bool = False, scans: "list[int] | None" = None,
) -> set:
    """add/commit intents found in git-subcommand position, recursing into eval, sh -c, heredoc,
    and substitution bodies. Location facts (`cd`, `-C`, GIT_DIR-style assignments) ride along.
    Past MAX_NESTING levels or MAX_SCANS bodies the answer is "too-deep", which refuses the call."""
    scans = [MAX_SCANS] if scans is None else scans
    scans[0] -= 1
    if depth > MAX_NESTING or scans[0] < 0:
        return {"too-deep"}
    intents = set()
    normalized, heredoc_bodies, substitutions = prepare(command, captured)
    marked = shell_tokens(normalized)
    tokens = [token.replace(COMPUTED, "") for token in marked]
    for index in _shell_body_indices(tokens):
        intents |= collect_intents(marked[index], depth + 1, scans=scans)
    for body in heredoc_bodies:
        intents |= collect_intents(body, depth + 1, scans=scans)
    for body in substitutions:
        intents |= collect_intents(body, depth + 1, True, scans)
    start = 0
    for end, token in enumerate(tokens + [";"]):
        if token not in CONTROL_TOKENS:
            continue
        segment, marked_segment, start = tokens[start:end], marked[start:end], end + 1
        intents |= _location_facts(marked_segment)
        # Every `git` token in the segment is inspected, not only the segment head: wrappers
        # (command, env, function bodies) and even `echo git add` stay conservatively covered.
        # Basename matching keeps path-qualified /usr/bin/git covered as well.
        for index, word in enumerate(segment):
            if _basename(word) == "git":
                found = _subcommand_index(segment, index)
                if found is None:
                    continue
                subcommand = segment[found]
                if subcommand == "commit" or subcommand in STAGING_SUBCOMMANDS:
                    intents.add(subcommand)
                elif subcommand == "tag":
                    tag_intent = _release_tag_intent(segment, found)
                    if tag_intent:
                        intents.add(tag_intent)
                        intents |= _git_location(marked_segment[index + 1:found])
                if subcommand in ("commit", "add"):
                    intents |= _git_location(marked_segment[index + 1:found])
    return intents


def unsafe_commit_args(arguments: list[str]) -> bool:
    consume_value = False
    consume_target = False
    for argument in arguments:
        if consume_value or consume_target:
            consume_value = consume_target = False
            continue
        if argument == "--":
            return True
        redirection = REDIRECTION_RE.match(argument)
        if redirection:
            # A separated operator (`> out`) owns the next token as its target; a glued one
            # (`>out`) carries it. Skipping the pair keeps scanning, so a pathspec or unsafe flag
            # written after a redirection is still reached. A target is tracked apart from an
            # option value because a dangling `>` at end of input names no pathspec, while a
            # dangling `-m` means the message was never supplied and the scan is incomplete.
            consume_target = not redirection.group(1)
            continue
        option, equals, _value = argument.partition("=")
        if option in UNSAFE_LONG:
            return True
        if option in VALUE_OPTIONS:
            consume_value = not equals
            continue
        if option in SAFE_LONG:
            continue
        if argument.startswith("--"):
            return True
        if argument.startswith("-") and argument != "-":
            flags = argument[1:]
            for index, flag in enumerate(flags):
                if flag in UNSAFE_SHORT:
                    return True
                if flag in "mFCc":
                    consume_value = index == len(flags) - 1
                    break
                if flag in "uS":
                    break
                if flag not in SAFE_SHORT:
                    return True
            continue
        return True
    return consume_value


def _has_process_substitution(text: str) -> bool:
    """True when `<(` or `>(` appears as syntax rather than inside a quoted word.

    The parentheses have to be found before tokenizing, because they are exactly what tokenizing
    trips over. Quoting still has to be honoured while looking, or `-m 'use <(cmd)'` reads as
    syntax and a message about process substitution refuses the commit that describes it.
    """
    quote = None
    for index, character in enumerate(text):
        if quote is not None:
            quote = None if character == quote else quote
            continue
        if character in "'\"":
            quote = character
            continue
        if text.startswith(PROCESS_SUBSTITUTION, index):
            return True
    return False


def has_unsafe_commit_mode(
    command: str, depth: int = 0, captured: bool = False, scans: "list[int] | None" = None,
) -> bool:
    scans = [MAX_SCANS] if scans is None else scans
    scans[0] -= 1
    if depth > MAX_NESTING or scans[0] < 0:
        return True
    normalized, heredoc_bodies, substitutions = prepare(command, captured)
    if _has_process_substitution(normalized):
        return True
    tokens = [token.replace(COMPUTED, "") for token in shell_tokens(normalized)]
    for index in _shell_body_indices(tokens):
        if has_unsafe_commit_mode(tokens[index], depth + 1, scans=scans):
            return True
    for body in heredoc_bodies:
        if has_unsafe_commit_mode(body, depth + 1, scans=scans):
            return True
    for body in substitutions:
        if has_unsafe_commit_mode(body, depth + 1, True, scans):
            return True
    segment: list[str] = []
    for token in tokens + [";"]:
        if token not in CONTROL_TOKENS:
            segment.append(token)
            continue
        for index, word in enumerate(segment):
            if word == "commit" and any(
                _basename(previous) == "git" for previous in segment[:index]
            ):
                if unsafe_commit_args(segment[index + 1:]):
                    return True
        segment = []
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        command = payload["tool_input"]["command"]
        if not isinstance(command, str):
            raise TypeError
    except (KeyError, TypeError, ValueError):
        print("none")
        return 0

    try:
        intents = collect_intents(command)
        unsafe = "commit" in intents and has_unsafe_commit_mode(command)
    except RecursionError:
        intents, unsafe = {"too-deep"}, True
    except AmbiguousSubstitution:
        # Its body could hide anything, but only a command that names git anywhere is refused.
        named = "git" in normalize_shell_word_joins(command)
        intents, unsafe = ({"too-deep"} if named else set()), named
    has_add = "add" in intents
    has_staging = bool(intents & STAGING_SUBCOMMANDS)
    has_commit = "commit" in intents
    release = intents & {"release-tag", "release-tag-elsewhere"}
    changes = [fact[1] for fact in intents if isinstance(fact, tuple) and fact[0] == "cd"]
    directories = set(changes)
    chains = {fact[1] for fact in intents if isinstance(fact, tuple) and fact[0] == "C"}
    # Order is lost in the set, so several directory changes resolve only when each is absolute:
    # then wherever the commit runs is one of them. `cd a && cd b` could be a/b, and `cd a && cd a`
    # is a/a, so both are refused.
    unresolved = "unresolved-target" in intents or (
        len(changes) > 1 and not all(os.path.isabs(path) for path in changes)
    )
    if "too-deep" in intents:
        print("unresolved")
    elif release and (has_staging or has_commit):
        # An evergreen--v* tag creation must be the only git operation in its call: the
        # certification gate exits before the staged-index inspection, so a combined add or
        # commit would otherwise ride past the hygiene backstop.
        print("release-compound")
    elif "release-tag-elsewhere" in intents:
        print("release-elsewhere")
    elif has_staging and has_commit:
        print("compound")
    elif unresolved and (has_commit or release):
        print("unresolved")
    elif unsafe:
        print("unsafe")
    elif not (has_add or has_commit or release):
        # Only add and commit ask for the staged-index inspection. A lone `git mv` or `git reset`
        # stages nothing this hook can judge yet; the commit that follows it is inspected, which
        # is the whole point of forcing the two into separate calls.
        print("none")
    else:
        # Every directory the command may run git in, beyond the hook's own root, one per line:
        # each literal `cd`, and each `-C` chain resolved from the root and from every `cd`.
        bases = [""] + sorted(directories)
        extra = directories | {os.path.join(base, chain) for base in bases for chain in chains}
        print("\n".join(["release-tag" if release else "git", *sorted(extra)]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
