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
bodies. A heredoc body is treated like a quoted argument — data for the command it feeds, whose
words create no intent — unless the introducing line hands it to a shell or to eval, in which case
it is code and is recursed into. Recursion can only add detected intent, never hide it.
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
# `<<WORD`, `<<'WORD'`, `<<"WORD"`, and the tab-stripping `<<-WORD`. `<<<` cannot match: the
# delimiter must open with a quote or an identifier character, and `<` is neither.
HEREDOC_RE = re.compile(r"<<-?[ \t]*(?:'([^']*)'|\"([^\"]*)\"|([A-Za-z_][A-Za-z0-9_]*))")


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _feeds_a_shell(line: str) -> bool:
    """True when the line introducing a heredoc hands its body to a shell or to eval.

    Deliberately coarse — any interpreter word anywhere on the line qualifies, so `cat <<EOF |
    bash` counts. Over-matching only adds recursion, and recursion only adds detected intent.
    """
    for token in shell_tokens(line):
        if token == "eval" or _basename(token) in SHELL_INTERPRETERS:
            return True
    return False


def split_heredocs(command: str) -> "tuple[str, list[str]]":
    """Separate heredoc bodies from the command text that introduces them.

    A heredoc body is DATA for the command it feeds — a commit message, a file payload — not part
    of that command's argument list, and scanning it as arguments misreads every word as a
    pathspec. This mirrors the rule already applied to quoted arguments: a `<<'EOF'` body is the
    shell's most literal quoting form, so words inside it no more create intent than words inside
    `-m '...'`. The exception is a body a shell will execute, which is code and is returned for
    recursion. An unterminated or mis-detected heredoc consumes to end of input; that only removes
    text from the argument scan, and the same text is still classified when a shell consumes it.
    """
    lines = command.split("\n")
    kept: list[str] = []
    executable: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        for match in HEREDOC_RE.finditer(line):
            delimiter = next(group for group in match.groups() if group is not None)
            strip_tabs = match.group(0).startswith("<<-")
            body: list[str] = []
            while index < len(lines):
                candidate = lines[index]
                index += 1
                compared = candidate.lstrip("\t") if strip_tabs else candidate
                if compared == delimiter:
                    break
                body.append(candidate)
            if _feeds_a_shell(line):
                executable.append("\n".join(body))
    return "\n".join(kept), executable


def prepare(command: str) -> "tuple[str, list[str]]":
    """Normalize joined lines, lift heredoc bodies out, and make newlines separate commands."""
    stripped, executable = split_heredocs(command.replace("\\\n", ""))
    return stripped.replace("\n", " ; "), executable


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
    for argument in arguments:
        if consume_value:
            consume_value = False
            continue
        if argument == "--":
            return True
        redirection = REDIRECTION_RE.match(argument)
        if redirection:
            # A separated operator (`> out`) owns the next token as its target; a glued one
            # (`>out`) carries it. Skipping the pair keeps scanning, so a pathspec or unsafe flag
            # written after a redirection is still reached.
            consume_value = not redirection.group(1)
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


def has_unsafe_commit_mode(command: str) -> bool:
    normalized, heredoc_bodies = prepare(command)
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
