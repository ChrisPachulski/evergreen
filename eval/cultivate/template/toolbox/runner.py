"""Small, ordinary formatting behavior."""
from __future__ import annotations


def normalize_title(value: str) -> str:
    return " ".join(word.capitalize() for word in value.split())
