#!/usr/bin/env python3
"""Classify decoded Bash tool input for the evergreen commit guard."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

CONTROL = {";", "\n", "&&", "||", "&", "(", ")"}
GIT_OPTIONS_WITH_VALUE = {
    "-C",
    "-c",
    "--config-env",
    "--exec-path",
    "--git-dir",
    "--namespace",
    "--work-tree",
}
SHELLS = {"bash", "dash", "ksh", "sh", "zsh"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def lex(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.commenters = "#"
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    return list(lexer)


class Parser:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.index = 0

    def peek(self) -> str | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> str:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def parse(self, stop: str | None = None):
        node = self.parse_and_or(stop)
        while self.peek() in {";", "\n", "&"}:
            operator = self.take()
            if self.peek() in {None, stop}:
                break
            node = (operator, node, self.parse_and_or(stop))
        return node

    def parse_and_or(self, stop: str | None):
        node = self.parse_primary(stop)
        while self.peek() in {"&&", "||"}:
            operator = self.take()
            node = (operator, node, self.parse_primary(stop))
        return node

    def parse_primary(self, stop: str | None):
        if self.peek() == "(":
            self.take()
            node = self.parse(")")
            if self.peek() == ")":
                self.take()
            return node

        words: list[str] = []
        while self.peek() is not None and self.peek() not in CONTROL and self.peek() != stop:
            words.append(self.take())
        return ("command", words)


def unwrap(words: list[str]) -> list[str]:
    words = list(words)
    while words:
        name = os.path.basename(words[0])
        if name == "command":
            words.pop(0)
            while words and words[0].startswith("-"):
                words.pop(0)
            continue
        if name == "env":
            words.pop(0)
            while words:
                if words[0] in {"-u", "--unset"} and len(words) > 1:
                    del words[:2]
                elif words[0].startswith("-") or ASSIGNMENT.match(words[0]):
                    words.pop(0)
                else:
                    break
            continue
        break
    return words


def git_intent(words: list[str]) -> str | None:
    words = unwrap(words)
    if not words or os.path.basename(words[0]) != "git":
        return None

    index = 1
    while index < len(words):
        token = words[index]
        if token in GIT_OPTIONS_WITH_VALUE:
            index += 2
        elif token.startswith("-"):
            index += 1
        else:
            return token if token in {"add", "commit"} else None
    return None


def shell_script(words: list[str]) -> str | None:
    words = unwrap(words)
    if not words or os.path.basename(words[0]) not in SHELLS:
        return None
    for index, token in enumerate(words[1:], start=1):
        if token.startswith("-") and "c" in token[1:] and index + 1 < len(words):
            return words[index + 1]
    return None


def run(node, inputs: set[bool]):
    kind = node[0]
    if kind == "command":
        words = node[1]
        nested = shell_script(words)
        if nested is not None:
            return evaluate(nested, inputs)

        intent = git_intent(words)
        if intent == "add":
            return {True}, set(inputs), True, False
        if intent == "commit":
            return set(inputs), set(inputs), True, True in inputs
        return set(inputs), set(inputs), False, False

    operator, left, right = node
    left_success, left_failure, left_git, left_compound = run(left, inputs)
    if operator in {";", "\n"}:
        right_success, right_failure, right_git, right_compound = run(
            right, left_success | left_failure
        )
        return (
            right_success,
            right_failure,
            left_git or right_git,
            left_compound or right_compound,
        )
    if operator == "&&":
        right_success, right_failure, right_git, right_compound = run(right, left_success)
        return (
            right_success,
            left_failure | right_failure,
            left_git or right_git,
            left_compound or right_compound,
        )
    if operator == "||":
        right_success, right_failure, right_git, right_compound = run(right, left_failure)
        return (
            left_success | right_success,
            right_failure,
            left_git or right_git,
            left_compound or right_compound,
        )

    # Backgrounded commands do not establish a finalized sequential index for the next command.
    right_success, right_failure, right_git, right_compound = run(right, inputs)
    return (
        right_success,
        right_failure,
        left_git or right_git,
        left_compound or right_compound,
    )


def evaluate(command: str, inputs: set[bool] | None = None):
    try:
        tree = Parser(lex(command)).parse()
    except (ValueError, IndexError):
        return set(), set(), False, False
    return run(tree, inputs if inputs is not None else {False})


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        command = payload["tool_input"]["command"]
        if not isinstance(command, str):
            return 0
    except (KeyError, TypeError, ValueError):
        return 0

    _, _, saw_git, compound = evaluate(command)
    print("compound" if compound else "git" if saw_git else "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
