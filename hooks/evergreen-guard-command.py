#!/usr/bin/env python3
"""Classify decoded Bash tool input for the evergreen commit guard.

The threat boundary is recognizable Git intent after simple quote/backslash word joining, not
arbitrary commands computed through variables, substitutions, aliases, or shell evaluation.
"""

import json
import re
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


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        command = payload["tool_input"]["command"]
        if not isinstance(command, str):
            raise TypeError
    except (KeyError, TypeError, ValueError):
        print("none")
        return 0

    command = normalize_shell_word_joins(command)
    has_add = has_git_intent(command, "add")
    has_commit = has_git_intent(command, "commit")
    print("compound" if has_add and has_commit else "git" if has_add or has_commit else "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
