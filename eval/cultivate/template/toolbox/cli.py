"""Console entry point for Toolbelt."""
from __future__ import annotations

from .runner import normalize_title


def main() -> None:
    print(normalize_title("toolbelt"))
