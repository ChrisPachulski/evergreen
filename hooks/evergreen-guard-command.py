#!/usr/bin/env python3
"""Classify decoded Bash tool input for the evergreen commit guard.

The threat boundary is recognizable Git intent in shlex-tokenized subcommand position — the
first non-option token after a `git` word — not arbitrary commands computed through variables,
substitutions, aliases, or shell evaluation. Words inside quoted arguments (commit messages,
pathspecs) are single tokens and never create intent. Unparseable input (unbalanced quotes)
degrades to coarse quote-stripped splitting, which may fail closed.

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

import json
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
UNSAFE_LONG = {
    "--all", "--include", "--only", "--interactive", "--patch",
    "--pathspec-from-file", "--pathspec-file-nul",
}
VALUE_OPTIONS = {
    "-m", "--message", "-F", "--file", "--author", "--date",
    "-C", "--reuse-message", "-c", "--reedit-message", "--fixup",
    "--squash", "--cleanup", "--trailer", "--gpg-sign",
    "--untracked-files",
}
SAFE_LONG = {
    "--quiet", "--verbose", "--no-verify", "--allow-empty",
    "--allow-empty-message", "--amend", "--no-post-rewrite", "--signoff",
    "--no-gpg-sign", "--dry-run", "--status", "--no-status", "--short",
    "--branch", "--porcelain", "--long", "--null", "--no-ahead-behind",
    "--ahead-behind", "--edit", "--no-edit", "--reset-author",
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
# before tokenizing; none of them can name a pathspec, so nothing is lost by flattening them.
AMPERSAND_REDIRECT_RE = re.compile(r"(?<![&|])&(>>?)")
FD_DUPLICATION_RE = re.compile(r"\d*[<>]&(?:\d+|-)")
FORCED_WRITE_RE = re.compile(r">\|")
# Process substitution puts a command inside an argument list. Its parentheses split the token
# stream, so the arguments after it cannot be scanned; a commit carrying one is refused instead.
PROCESS_SUBSTITUTION = ("<(", ">(")
# A heredoc operator has to begin a word. Anything else is text inside one: `echo a<<b` redirects
# nothing. A file-descriptor digit may precede it, so `2<<EOF` still opens at the space before `2`.
OPERATOR_BOUNDARY = set(" \t;&|()")
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


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _opens_a_word(line: str, index: int) -> bool:
    """True when the `<<` at index begins a word, so it can be an operator rather than text.

    `echo a<<b` redirects nothing — the `<<` is glued into a word and the shell reads it as such.
    Only a file-descriptor digit may sit between the operator and the boundary before it.
    """
    start = index
    while start and line[start - 1].isdigit():
        start -= 1
    return start == 0 or line[start - 1] in OPERATOR_BOUNDARY


def _heredoc_operators(line: str) -> "list[tuple[str, bool]]":
    """(delimiter, strips_tabs) for each real heredoc operator on one line.

    Walking the line beats scanning it with a regex because only a walk knows what is quoted: a
    `<<` inside `-m "shift << operator"` introduces nothing, and treating it as an operator is how
    a commit message came to look like a heredoc. `<<<` is a here-string and is stepped over.
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
        if not _opens_a_word(line, index):
            index += 2
            continue
        match = HEREDOC_RE.match(line, index)
        if not match:
            index += 2
            continue
        delimiter = next(group for group in match.groups() if group is not None)
        operators.append((delimiter, line.startswith("<<-", index)))
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


def _body_is_data(segment: str) -> bool:
    """True when the heredoc body this command segment introduces cannot become shell commands.

    The default is False, and that direction is the whole point: a body scanned needlessly costs a
    false positive, while a body skipped wrongly is a hole, and an allowlist of shells could not
    know about `fish`, `sh<<EOF`, or `. /dev/stdin`. Only three shapes are provably inert — a
    `git commit` reading its message, a script for an interpreter that is not the shell, and a
    payload copied into a file — and none of them counts on a line that also reaches a shell,
    `eval`, or `source`, which is why those are checked first.
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
    return basename in STDIN_WRITERS and _redirects_to_file(segment)


def split_heredocs(command: str) -> "tuple[str, list[str]]":
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
    """
    lines = command.split("\n")
    kept: list[str] = []
    scannable: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        line_reaches_a_shell = _feeds_a_shell(line)
        for segment in _line_segments(line):
            for delimiter, strip_tabs in _heredoc_operators(segment):
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
                if line_reaches_a_shell or not _body_is_data(segment):
                    scannable.append("\n".join(body))
    return "\n".join(kept), scannable


def prepare(command: str) -> "tuple[str, list[str]]":
    """Normalize joined lines, lift heredoc bodies out, and make newlines separate commands."""
    stripped, scannable = split_heredocs(command.replace("\\\n", ""))
    stripped = AMPERSAND_REDIRECT_RE.sub(r"\1", stripped)
    stripped = FD_DUPLICATION_RE.sub(">fd", stripped)
    stripped = FORCED_WRITE_RE.sub(">", stripped)
    return stripped.replace("\n", " ; "), scannable


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


def git_subcommand(segment: list[str], git_index: int) -> "str | None":
    """First non-option token after segment[git_index] (a `git` word), skipping global options."""
    index = git_index + 1
    while index < len(segment):
        token = segment[index]
        if token == "--":
            return None
        if token.startswith("-") and token != "-":
            option, equals, _value = token.partition("=")
            index += 2 if option in GIT_VALUE_GLOBALS and not equals else 1
            continue
        return token
    return None


def collect_intents(command: str) -> set:
    """add/commit intents found in git-subcommand position, recursing into eval and sh -c."""
    intents = set()
    normalized, heredoc_bodies = prepare(command)
    tokens = shell_tokens(normalized)
    for index in _shell_body_indices(tokens):
        intents |= collect_intents(tokens[index])
    for body in heredoc_bodies:
        intents |= collect_intents(body)
    segment: list[str] = []
    for token in tokens + [";"]:
        if token not in CONTROL_TOKENS:
            segment.append(token)
            continue
        # Every `git` token in the segment is inspected, not only the segment head: wrappers
        # (command, env, function bodies) and even `echo git add` stay conservatively covered.
        # Basename matching keeps path-qualified /usr/bin/git covered as well.
        for index, word in enumerate(segment):
            if _basename(word) == "git":
                subcommand = git_subcommand(segment, index)
                if subcommand in {"add", "commit"}:
                    intents.add(subcommand)
        segment = []
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


def has_unsafe_commit_mode(command: str) -> bool:
    normalized, heredoc_bodies = prepare(command)
    if _has_process_substitution(normalized):
        return True
    tokens = shell_tokens(normalized)
    for index in _shell_body_indices(tokens):
        if has_unsafe_commit_mode(tokens[index]):
            return True
    for body in heredoc_bodies:
        if has_unsafe_commit_mode(body):
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

    intents = collect_intents(command)
    has_add = "add" in intents
    has_commit = "commit" in intents
    if has_add and has_commit:
        print("compound")
    elif has_commit and has_unsafe_commit_mode(command):
        print("unsafe")
    else:
        print("git" if has_add or has_commit else "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
