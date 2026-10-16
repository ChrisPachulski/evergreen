#!/usr/bin/env python3
"""Command-line entry point for the Evergreen benchmark."""

try:
    from .runner import main
except ImportError:  # Direct script execution.
    from runner import main


if __name__ == "__main__":
    raise SystemExit(main())
