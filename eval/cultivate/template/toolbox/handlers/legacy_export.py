"""One candidate handler. Whether it is the live one depends on deployment configuration."""
from __future__ import annotations


def export(rows: list[str]) -> str:
    return "\n".join(rows)
