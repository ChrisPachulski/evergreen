import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "eval" / "peers-v1.json"


def canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def request(rows):
    document = {
        "schema_version": 1,
        "kind": "evergreen-peer-input",
        "id_set_sha256": "a" * 64,
        "rows": rows,
    }
    document["input_sha256"] = hashlib.sha256(canonical_bytes(document)).hexdigest()
    return document


def row(index, language="typescript", documentation="current documentation"):
    return {
        "opaque_id": f"{index:064x}",
        "language": language,
        "code": "export function current(): number { return 1; }",
        "documentation": documentation,
    }


def write_drift_guardian_fixture(checkout):
    module = checkout / "src" / "detectors" / "docsDrift.js"
    module.parent.mkdir(parents=True)
    module.write_text(
        """
const fs = require('fs');
const path = require('path');
exports.detectDocsDrift = async function(options) {
  if (options.llm !== null || options.changedFiles.length !== 1) {
    throw new Error('adapter did not use the pinned API contract');
  }
  const docs = fs.readFileSync(path.join(options.repoRoot, 'README.md'), 'utf8');
  return docs.includes('current') ? [] : [{kind: 'docs-drift'}];
};
""".strip()
        + "\n"
    )


def write_documentation_detector_fixture(runtime):
    files = {
        "out/analyzers/AnalyzerFactory.js": """
class AnalyzerFactory {
  getAnalyzer(filename) {
    if (!filename.endsWith('.ts')) throw new Error('expected TypeScript');
    return {analyze(code, path) { return {code, path}; }};
  }
}
module.exports = {AnalyzerFactory};
""",
        "out/scanners/DocumentationIndex.js": """
const fs = require('fs');
const path = require('path');
class DocumentationIndex {
  constructor(workspace, ignored, options) {
    this.workspace = workspace;
    this.options = options;
  }
  async build() {
    if (this.options.scanPaths[0] !== 'README.md') throw new Error('bad scan path');
    return {documentation: fs.readFileSync(path.join(this.workspace, 'README.md'), 'utf8')};
  }
}
module.exports = {DocumentationIndex};
""",
        "out/services/DocumentationDriftDetector.js": """
class DocumentationDriftDetector {
  detect(analysis, index) {
    return {findings: index.documentation.includes('current') ? [] : [{kind: 'docs-drift'}]};
  }
}
module.exports = {DocumentationDriftDetector};
""",
    }
    for relative, contents in files.items():
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents.strip() + "\n")


class PeerAdapterTests(unittest.TestCase):
    def test_drift_guardian_invokes_pinned_api_and_emits_exact_bound_decisions(self):
        from eval import peers
        from eval.peer_adapters import drift_guardian

        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / "checkout"
            write_drift_guardian_fixture(checkout)
            frozen = request([
                row(1, "go"),
                row(2, "java", "stale documentation"),
                row(3, "python"),
                row(4, "typescript", "stale documentation"),
            ])
            with mock.patch.object(peers, "verify_git_source", return_value=True) as verify:
                output = json.loads(drift_guardian.run_bytes(
                    canonical_bytes(frozen), checkout=checkout, timeout=10,
                ))

        peers.validate_output(output, frozen)
        self.assertEqual(
            output,
            {
                "schema_version": 1,
                "kind": "evergreen-peer-decisions",
                "input_sha256": frozen["input_sha256"],
                "rows": [
                    {"opaque_id": row(1)["opaque_id"], "decision": "consistent"},
                    {"opaque_id": row(2)["opaque_id"], "decision": "inconsistent"},
                    {"opaque_id": row(3)["opaque_id"], "decision": "consistent"},
                    {"opaque_id": row(4)["opaque_id"], "decision": "inconsistent"},
                ],
            },
        )
        source = next(
            item["source"] for item in peers.load_manifest(MANIFEST)["peers"]
            if item["id"] == "drift-guardian"
        )
        verify.assert_called_once_with(source, checkout)

    def test_adapter_rejects_private_fields_and_unsupported_languages_before_execution(self):
        from eval import peers
        from eval.peer_adapters import drift_guardian

        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            private = request([row(1)])
            private["rows"][0]["label"] = "consistent"
            private["input_sha256"] = hashlib.sha256(
                canonical_bytes({key: value for key, value in private.items()
                                 if key != "input_sha256"})
            ).hexdigest()
            rust = request([row(2, "rust")])
            with mock.patch.object(peers, "verify_git_source") as verify:
                with self.assertRaisesRegex(peers.PeerError, "fields"):
                    drift_guardian.run_bytes(canonical_bytes(private), checkout=checkout)
                with self.assertRaisesRegex(peers.PeerError, "not applicable"):
                    drift_guardian.run_bytes(canonical_bytes(rust), checkout=checkout)
                verify.assert_not_called()

    def test_documentation_detector_requires_verified_external_runtime_receipt(self):
        from eval import peers
        from eval.peer_adapters import common, documentation_drift_detector as detector

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout"
            checkout.mkdir()
            runtime = root / "runtime"
            write_documentation_detector_fixture(runtime)
            receipt = common.make_runtime_receipt(
                "documentation-drift-detector", runtime, manifest_path=MANIFEST,
            )
            receipt_path = root / "runtime-receipt.json"
            receipt_bytes = canonical_bytes(receipt)
            receipt_path.write_bytes(receipt_bytes)
            receipt_hash = hashlib.sha256(receipt_bytes).hexdigest()
            frozen = request([row(1), row(2, documentation="stale documentation")])
            with mock.patch.object(peers, "verify_git_source", return_value=True):
                output = json.loads(detector.run_bytes(
                    canonical_bytes(frozen), checkout=checkout, runtime=runtime,
                    runtime_receipt=receipt_path,
                    runtime_receipt_sha256=receipt_hash, timeout=10,
                ))

        peers.validate_output(output, frozen)
        self.assertEqual(
            [item["decision"] for item in output["rows"]],
            ["consistent", "inconsistent"],
        )

    def test_documentation_detector_rejects_wrong_receipt_hash_and_runtime_drift(self):
        from eval import peers
        from eval.peer_adapters import common, documentation_drift_detector as detector

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout"
            checkout.mkdir()
            runtime = root / "runtime"
            write_documentation_detector_fixture(runtime)
            receipt = common.make_runtime_receipt(
                "documentation-drift-detector", runtime, manifest_path=MANIFEST,
            )
            receipt_path = root / "receipt.json"
            receipt_bytes = canonical_bytes(receipt)
            receipt_path.write_bytes(receipt_bytes)
            frozen = request([row(1)])
            with mock.patch.object(peers, "verify_git_source", return_value=True):
                with self.assertRaisesRegex(peers.PeerError, "receipt hash"):
                    detector.run_bytes(
                        canonical_bytes(frozen), checkout=checkout, runtime=runtime,
                        runtime_receipt=receipt_path,
                        runtime_receipt_sha256="0" * 64,
                    )
                target = runtime / "out" / "services" / "DocumentationDriftDetector.js"
                target.write_text(target.read_text() + "// changed\n")
                with self.assertRaisesRegex(peers.PeerError, "inventory"):
                    detector.run_bytes(
                        canonical_bytes(frozen), checkout=checkout, runtime=runtime,
                        runtime_receipt=receipt_path,
                        runtime_receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
                    )

    def test_documentation_detector_detects_runtime_mutation_during_execution(self):
        from eval import peers
        from eval.peer_adapters import common, documentation_drift_detector as detector

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout"
            checkout.mkdir()
            runtime = root / "runtime"
            write_documentation_detector_fixture(runtime)
            target = runtime / "out" / "services" / "DocumentationDriftDetector.js"
            target.write_text(
                target.read_text().replace(
                    "detect(analysis, index) {",
                    "detect(analysis, index) { require('fs').appendFileSync(__filename, ' ');",
                )
            )
            receipt = common.make_runtime_receipt(
                "documentation-drift-detector", runtime, manifest_path=MANIFEST,
            )
            receipt_path = root / "receipt.json"
            receipt_bytes = canonical_bytes(receipt)
            receipt_path.write_bytes(receipt_bytes)
            with mock.patch.object(peers, "verify_git_source", return_value=True):
                with self.assertRaisesRegex(peers.PeerError, "inventory"):
                    detector.run_bytes(
                        canonical_bytes(request([row(1)])), checkout=checkout,
                        runtime=runtime, runtime_receipt=receipt_path,
                        runtime_receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(),
                    )

    def test_documentation_detector_rejects_checkout_as_runtime(self):
        from eval import peers
        from eval.peer_adapters import documentation_drift_detector as detector

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen = request([row(1)])
            receipt = root / "receipt.json"
            receipt.write_text("{}")
            with self.assertRaisesRegex(peers.PeerError, "distinct"):
                detector.run_bytes(
                    canonical_bytes(frozen), checkout=root, runtime=root,
                    runtime_receipt=receipt, runtime_receipt_sha256="0" * 64,
                )


if __name__ == "__main__":
    unittest.main()
