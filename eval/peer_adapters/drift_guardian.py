"""CLI adapter for drift-guardian at its frozen upstream commit."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from eval.peer_adapters import common


RUNNER = Path(__file__).with_name("drift_guardian_runner.js")
LANGUAGES = ("go", "java", "python", "typescript")


def run_bytes(payload, *, checkout, timeout=300, manifest_path=common.DEFAULT_MANIFEST):
    return common.run_adapter(
        peer_id="drift-guardian", payload=payload, checkout=checkout, runner=RUNNER,
        applicable_languages=LANGUAGES, timeout=timeout, manifest_path=manifest_path,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300)
    arguments = parser.parse_args(argv)
    try:
        output = run_bytes(
            sys.stdin.buffer.read(common.MAX_INPUT_BYTES + 1),
            checkout=arguments.checkout, timeout=arguments.timeout,
        )
    except ValueError as error:
        parser.error(str(error))
    sys.stdout.buffer.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
