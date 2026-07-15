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

if __name__ == "__main__":
    unittest.main()
