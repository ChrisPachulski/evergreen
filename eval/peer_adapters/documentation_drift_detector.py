"""CLI adapter for Documentation Drift Detector at its frozen upstream commit."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from eval import peers
from eval.peer_adapters import common


RUNNER = Path(__file__).with_name("documentation_drift_detector_runner.js")


def run_bytes(payload, *, checkout, runtime, runtime_receipt,
              runtime_receipt_sha256, timeout=300,
              manifest_path=common.DEFAULT_MANIFEST):
    checkout = Path(checkout).resolve()
    runtime = Path(runtime).resolve()
    if checkout == runtime:
        raise peers.PeerError("peer source checkout and runtime must be distinct")
    common.verify_runtime_receipt(
        "documentation-drift-detector", runtime, runtime_receipt,
        runtime_receipt_sha256, manifest_path=manifest_path,
    )
    result = common.run_adapter(
        peer_id="documentation-drift-detector", payload=payload, checkout=checkout,
        runner=RUNNER, applicable_languages=("typescript",), runner_arguments=(runtime,),
        timeout=timeout, manifest_path=manifest_path,
    )
    common.verify_runtime_receipt(
        "documentation-drift-detector", runtime, runtime_receipt,
        runtime_receipt_sha256, manifest_path=manifest_path,
    )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    parser.add_argument("--runtime-receipt-sha256", required=True)
    parser.add_argument("--timeout", type=float, default=300)
    arguments = parser.parse_args(argv)
    try:
        output = run_bytes(
            sys.stdin.buffer.read(common.MAX_INPUT_BYTES + 1),
            checkout=arguments.checkout, runtime=arguments.runtime,
            runtime_receipt=arguments.runtime_receipt,
            runtime_receipt_sha256=arguments.runtime_receipt_sha256,
            timeout=arguments.timeout,
        )
    except ValueError as error:
        parser.error(str(error))
    sys.stdout.buffer.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
