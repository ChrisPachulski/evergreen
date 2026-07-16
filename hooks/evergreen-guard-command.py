#!/usr/bin/env python3
"""Classify decoded Bash tool input for the evergreen commit guard.

The threat boundary is recognizable Git intent in shlex-tokenized subcommand position — the
first non-option token after a `git` word — not arbitrary commands computed through variables,
substitutions, aliases, or shell evaluation. Words inside quoted arguments (commit messages,
pathspecs) are single tokens and never create intent. Unparseable input (unbalanced quotes)
degrades to coarse quote-stripped splitting, which may fail closed.
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


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


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
    tokens = shell_tokens(command.replace("\\\n", ""))
    for index in _shell_body_indices(tokens):
        intents |= collect_intents(tokens[index])
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
    tokens = shell_tokens(command)
    for index in _shell_body_indices(tokens):
        if has_unsafe_commit_mode(tokens[index]):
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
