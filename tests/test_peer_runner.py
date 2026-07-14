import copy
import json
from pathlib import Path
import tempfile
import unittest

from tests.test_peers import rows


class PeerRunnerTests(unittest.TestCase):
    def test_child_rederives_manifest_config_and_source_hashes(self):
        from eval import peers
        from eval.bench import run_peer

        manifest = peers.load_manifest(Path(__file__).resolve().parents[1] / "eval/peers-v1.json")
        peer = next(item for item in manifest["peers"] if item["id"] == "drift-guardian")
        settings = {
            "peer_id": peer["id"],
            "peer_manifest_sha256": peers._manifest_sha256(manifest),
            "peer_config_sha256": peer["config_sha256"],
            "peer_source_sha256": peers._source_sha256(peer["source"]),
        }
        _loaded, selected = run_peer.bound_peer(settings)
        self.assertEqual(selected, peer)
        for field in ("peer_manifest_sha256", "peer_config_sha256", "peer_source_sha256"):
            forged = dict(settings)
            forged[field] = "0" * 64
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "match"):
                run_peer.bound_peer(forged)

    def test_direct_baseline_is_one_label_blind_model_call(self):
        from eval import peers
        from eval.bench import run_peer

        request = peers.freeze_request(rows(), b"s" * 32)
        item = request["rows"][0]
        calls = []

        def call(prompt, model, provider):
            calls.append((prompt, model, provider))
            return {"status": "ok", "value": {
                "opaque_id": item["opaque_id"], "decision": "consistent",
            }}

        self.assertEqual(
            run_peer.direct_decision(item, "model-v1", "codex", call=call),
            {"opaque_id": item["opaque_id"], "decision": "consistent"},
        )
        self.assertEqual(len(calls), 1)
        self.assertNotIn("private-", calls[0][0])
        self.assertNotIn('"label"', calls[0][0])
        self.assertNotIn('"project"', calls[0][0])
        self.assertNotIn('"mutation_id"', calls[0][0])

    def test_direct_baseline_malformed_or_identity_changed_response_abstains(self):
        from eval import peers
        from eval.bench import run_peer

        item = peers.freeze_request(rows(), b"s" * 32)["rows"][0]
        cases = [
            {"status": "abstain", "reason": "timeout"},
            {"status": "ok", "value": {"opaque_id": "f" * 64,
                                         "decision": "consistent"}},
            {"status": "ok", "value": {"opaque_id": item["opaque_id"],
                                         "decision": "consistent", "why": "extra"}},
        ]
        for result in cases:
            with self.subTest(result=result):
                observed = run_peer.direct_decision(
                    item, "model-v1", "codex", call=lambda *_args, value=result: value,
                )
                self.assertEqual(observed, {
                    "opaque_id": item["opaque_id"], "decision": "abstain",
                })

    def test_direct_batch_resumes_only_missing_opaque_ids_and_emits_exact_output(self):
        from eval import peers
        from eval.bench import run_peer

        request = peers.freeze_request(rows(), b"s" * 32)
        first = request["rows"][0]
        previous = {first["opaque_id"]: "consistent"}
        calls = []

        def decide(item, _model, _provider):
            calls.append(item["opaque_id"])
            return {"opaque_id": item["opaque_id"], "decision": "consistent"}

        output = run_peer.run_direct(request, "model-v1", "codex", previous, decide=decide)
        self.assertNotIn(first["opaque_id"], calls)
        self.assertEqual(len(calls), len(request["rows"]) - 1)
        self.assertEqual(len(peers.validate_output(output, request)), len(request["rows"]))

    def test_local_adapter_receives_only_canonical_opaque_request_and_is_checkpointed(self):
        from eval import peers
        from eval.bench import run_peer

        source = [item for item in rows() if item["language"] == "typescript"]
        request = peers.freeze_request(source, b"s" * 32)
        calls = []
        checkpoints = []

        def execute(payload, checkout):
            calls.append((payload, checkout))
            parsed = json.loads(payload)
            return peers.canonical_bytes({
                "schema_version": 1,
                "kind": "evergreen-peer-decisions",
                "input_sha256": parsed["input_sha256"],
                "rows": [
                    {"opaque_id": item["opaque_id"], "decision": "consistent"}
                    for item in parsed["rows"]
                ],
            })

        with tempfile.TemporaryDirectory() as directory:
            output = run_peer.run_local(
                request, "drift-guardian", directory, execute=execute,
                checkpoint=checkpoints.append,
            )
        peers.validate_output(output, request)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], peers.canonical_bytes(request))
        self.assertNotIn(b'"label"', calls[0][0])
        self.assertNotIn(b"private-", calls[0][0])
        self.assertEqual(set(checkpoints[0]), {
            item["opaque_id"] for item in request["rows"]
        })

    def test_local_adapter_rejects_nonbytes_and_invalid_decisions(self):
        from eval import peers
        from eval.bench import run_peer

        request = peers.freeze_request(
            [item for item in rows() if item["language"] == "typescript"], b"s" * 32,
        )
        with self.assertRaisesRegex(ValueError, "output"):
            run_peer.run_local(
                request, "drift-guardian", ".", execute=lambda *_args: {},
            )
        with self.assertRaises(peers.PeerError):
            run_peer.run_local(
                request, "drift-guardian", ".",
                execute=lambda *_args: b'{"schema_version":1}',
            )

    def test_peer_settings_are_exact_and_reject_self_asserted_success(self):
        from eval.bench import run_peer

        valid = {
            "provider": "codex", "model": "model-v1", "peer_id": "direct-baseline",
            "peer_manifest_sha256": "a" * 64, "peer_config_sha256": "b" * 64,
            "peer_source_sha256": "c" * 64, "concurrency": 2,
            "resolver": "v2", "context_protocol": "none",
            "split_manifest_sha256": "d" * 64, "split": "holdout",
        }
        self.assertEqual(run_peer.validate_settings(copy.deepcopy(valid)), valid)
        invalid = copy.deepcopy(valid)
        invalid["passed"] = True
        with self.assertRaises(ValueError):
            run_peer.validate_settings(invalid)


if __name__ == "__main__":
    unittest.main()
