#!/usr/bin/env python3
"""Command-line entry point for the Evergreen benchmark."""

try:
    from .runner import main, selftest
except ImportError:  # Direct script execution.
    from runner import main, selftest


if __name__ == "__main__":
    raise SystemExit(main())
