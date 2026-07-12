#!/usr/bin/env python3
"""Classify decoded Bash tool input for the evergreen commit guard.

The threat boundary is recognizable Git intent after simple quote/backslash word joining, not
arbitrary commands computed through variables, substitutions, aliases, or shell evaluation.
"""

import json
import re
import shlex
import sys


def normalize_shell_word_joins(command: str) -> str:
    command = command.replace("\\\n", "")
    command = re.sub(r"\\(.)", r"\1", command, flags=re.DOTALL)
    return command.replace("'", "").replace('"', "")


def has_git_intent(command: str, intent: str) -> bool:
    return bool(
        re.search(
            rf"(?<![\w-])git\b.*?(?<![\w-]){intent}\b",
            command,
            flags=re.DOTALL,
        )
    )


CONTROL_TOKENS = {";", "&&", "||", "&", "|", "(", ")", "{", "}"}
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


def shell_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|(){}")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return normalize_shell_word_joins(command).split()


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
    for index, token in enumerate(tokens):
        if index and tokens[index - 1] == "eval" and has_unsafe_commit_mode(token):
            return True
        if index >= 2 and tokens[index - 1] == "-c" and tokens[index - 2] in {
            "sh", "bash", "zsh",
        } and has_unsafe_commit_mode(token):
            return True
    segment: list[str] = []
    for token in tokens + [";"]:
        if token not in CONTROL_TOKENS:
            segment.append(token)
            continue
        for index, word in enumerate(segment):
            if word == "commit" and "git" in segment[:index]:
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

    normalized = normalize_shell_word_joins(command)
    has_add = has_git_intent(normalized, "add")
    has_commit = has_git_intent(normalized, "commit")
    if has_add and has_commit:
        print("compound")
    elif has_commit and has_unsafe_commit_mode(command):
        print("unsafe")
    else:
        print("git" if has_add or has_commit else "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
