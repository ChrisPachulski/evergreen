import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "eval" / "peers-v1.json"
LANGUAGES = ("go", "java", "python", "rust", "typescript")


def rows():
    values = []
    for language in LANGUAGES:
        values.extend((
            {
                "id": f"private-{language}-negative",
                "group_id": f"group-{language}",
                "project": f"org/{language}",
                "language": language,
                "variant": "source",
                "code": f"{language} source code",
                "documentation": f"{language} returns one",
                "oracle_kind": "return-value",
                "mutation_id": None,
                "label": "consistent",
            },
            {
                "id": f"private-{language}-positive",
                "group_id": f"group-{language}",
                "project": f"org/{language}",
                "language": language,
                "variant": "mutation",
                "code": f"{language} mutated code",
                "documentation": f"{language} returns one",
                "oracle_kind": "return-value",
                "mutation_id": "return-value-1-to-2-v1",
                "label": "inconsistent",
            },
        ))
    return values


def peer_bundle(manifest, peer_id, source_rows=None):
    from eval import peers

    peer = next(item for item in manifest["peers"] if item["id"] == peer_id)
    applicable = {
        language for language, state in peer["applicability"].items()
        if state["state"] == "applicable"
    }
    private_rows = [
        row for row in (source_rows or rows()) if row["language"] in applicable
    ]
    secret = b"s" * 32
    request = peers.freeze_request(private_rows, secret)
    output = {
        "schema_version": 1,
        "kind": "evergreen-peer-decisions",
        "input_sha256": request["input_sha256"],
        "rows": [
            {
                "opaque_id": item["opaque_id"],
                "decision": "inconsistent" if "mutated" in item["code"] else "consistent",
            }
            for item in request["rows"]
        ],
    }
    result = peers.score_output(
        manifest=manifest, peer_id=peer_id, subject_commit="a" * 40,
        private_rows=private_rows, secret=secret, request=request, output=output,
    )
    return {
        "private_rows": private_rows, "secret": secret, "request": request,
        "output": output, "result": result,
    }


class PeerProtocolTests(unittest.TestCase):
    def test_frozen_manifest_has_exact_sources_configs_and_applicability(self):
        from eval import peers

        manifest = peers.load_manifest(MANIFEST)
        self.assertEqual(manifest["languages"], list(LANGUAGES))
        self.assertEqual(
            manifest["exclusions"],
            [{
                "id": "documentation-drift-detector",
                "source_url": "https://github.com/Damienb123/Documentation-Drift-Detector.git",
                "source_commit": "9237c9c28dd6d884dd3f8d29998933c4dadde403",
                "reason_code": "runtime-receipt-unfrozen",
                "detail": (
                    "The pinned source requires a built Node runtime, but no reproducible "
                    "runtime receipt is frozen as a release input. Declaring it runnable would "
                    "make the comparison irreproducible."
                ),
            }],
        )
        by_id = {item["id"]: item for item in manifest["peers"]}
        self.assertEqual(
            set(by_id),
            {"direct-baseline", "drift-guardian"},
        )
        self.assertEqual(
            by_id["drift-guardian"]["source"]["commit"],
            "444021d47ce1c0319c6532488ec4cb886c9ac472",
        )
        self.assertEqual(
            by_id["drift-guardian"]["config_sha256"],
            "d9479704c46c069d4d548a71bd9d06e8d949be193e2db48d07546613a4a7c06a",
        )
        for item in manifest["peers"]:
            self.assertRegex(item["config_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(set(item["applicability"]), set(LANGUAGES))
            for language, state in item["applicability"].items():
                self.assertEqual(set(state), {"state", "reason"})
                if state["state"] == "applicable":
                    self.assertEqual(state["reason"], "")
                else:
                    self.assertTrue(state["reason"])
        self.assertTrue(all(
            state["state"] == "applicable"
            for state in by_id["direct-baseline"]["applicability"].values()
        ))

    def test_manifest_rejects_unknown_fields_hash_drift_and_missing_direct_baseline(self):
        from eval import peers

        document = json.loads(MANIFEST.read_text())
        cases = []
        unknown = copy.deepcopy(document)
        unknown["peers"][0]["trusted"] = True
        cases.append(unknown)
        missing = copy.deepcopy(document)
        missing["peers"] = [item for item in missing["peers"]
                            if item["id"] != "direct-baseline"]
        cases.append(missing)
        bad_hash = copy.deepcopy(document)
        bad_hash["peers"][1]["config_sha256"] = "0" * 64
        cases.append(bad_hash)
        missing_reason = copy.deepcopy(document)
        target = missing_reason["peers"][1]["applicability"]["rust"]
        target["reason"] = ""
        cases.append(missing_reason)
        for index, case in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(peers.PeerError):
                peers.load_manifest_bytes(
                    json.dumps(case, sort_keys=True, separators=(",", ":")).encode()
                )

    def test_opaque_request_strips_private_identity_outcomes_and_labels(self):
        from eval import peers

        first = peers.freeze_request(rows(), b"s" * 32)
        second = peers.freeze_request(list(reversed(rows())), b"s" * 32)
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(len(first["rows"]), len(rows()))
        self.assertEqual(
            set(first), {"schema_version", "kind", "id_set_sha256", "input_sha256", "rows"}
        )
        forbidden = {
            "id", "group_id", "project", "variant", "oracle_kind", "mutation_id",
            "label", "verdict", "expected", "correct",
        }
        for item in first["rows"]:
            self.assertFalse(forbidden & set(item))
            self.assertEqual(set(item), {"opaque_id", "language", "code", "documentation"})
            self.assertRegex(item["opaque_id"], r"^[0-9a-f]{64}$")
        raw = peers.canonical_bytes(first)
        self.assertNotIn(b"private-", raw)
        self.assertNotIn(b"consistent\"", raw)
        self.assertNotIn(b"org/", raw)

    def test_output_requires_exact_bound_ids_and_one_bounded_decision_each(self):
        from eval import peers

        request = peers.freeze_request(rows(), b"s" * 32)
        valid = {
            "schema_version": 1,
            "kind": "evergreen-peer-decisions",
            "input_sha256": request["input_sha256"],
            "rows": [
                {"opaque_id": item["opaque_id"], "decision": "consistent"}
                for item in request["rows"]
            ],
        }
        self.assertEqual(len(peers.validate_output(valid, request)), len(rows()))
        cases = []
        for mutation in ("missing", "duplicate", "extra", "stale", "label-aware"):
            candidate = copy.deepcopy(valid)
            if mutation == "missing":
                candidate["rows"].pop()
            elif mutation == "duplicate":
                candidate["rows"][-1] = copy.deepcopy(candidate["rows"][0])
            elif mutation == "extra":
                candidate["rows"].append({"opaque_id": "f" * 64, "decision": "consistent"})
            elif mutation == "stale":
                candidate["input_sha256"] = "0" * 64
            else:
                candidate["rows"][0]["label"] = "consistent"
            cases.append((mutation, candidate))
        for name, case in cases:
            with self.subTest(name=name), self.assertRaises(peers.PeerError):
                peers.validate_output(case, request)

    def test_peer_checkpoint_contains_only_opaque_decisions_and_revalidates_raw_rows(self):
        from eval import peers

        request = peers.freeze_request(rows(), b"s" * 32)
        decisions = [
            {"opaque_id": item["opaque_id"], "decision": "consistent"}
            for item in request["rows"][:3]
        ]
        metadata = {"frozen": "identity"}
        document = peers.run_document(
            metadata, request, decisions, started_at="2026-07-14T12:00:00Z",
            elapsed_seconds=1.25, provider_usage=None,
        )
        self.assertEqual(
            peers.validate_run_document(document, metadata, request),
            {item["opaque_id"]: item["decision"] for item in decisions},
        )
        raw = peers.canonical_bytes(document)
        for forbidden in (b'"label"', b'"project"', b'"mutation_id"', b'private-'):
            self.assertNotIn(forbidden, raw)
        forged = copy.deepcopy(document)
        forged["rows"][0]["label"] = "consistent"
        with self.assertRaises(peers.PeerError):
            peers.validate_run_document(forged, metadata, request)

    def test_results_are_scored_from_private_oracle_labels_not_peer_claims(self):
        from eval import peers

        source = rows()
        request = peers.freeze_request(source, b"s" * 32)
        decisions = []
        for item in request["rows"]:
            decisions.append({
                "opaque_id": item["opaque_id"],
                "decision": "inconsistent" if "mutated" in item["code"] else "consistent",
            })
        output = {
            "schema_version": 1,
            "kind": "evergreen-peer-decisions",
            "input_sha256": request["input_sha256"],
            "rows": list(reversed(decisions)),
        }
        manifest = peers.load_manifest(MANIFEST)
        result = peers.score_output(
            manifest=manifest, peer_id="direct-baseline", subject_commit="a" * 40,
            private_rows=source,
            secret=b"s" * 32, request=request, output=output,
        )
        self.assertEqual(result["id_set_sha256"], request["id_set_sha256"])
        self.assertEqual(result["subject_commit"], "a" * 40)
        for language in LANGUAGES:
            self.assertEqual(
                {key: result["languages"][language][key] for key in ("tp", "fp", "fn", "tn")},
                {"tp": 1, "fp": 0, "fn": 0, "tn": 1},
            )
        self.assertNotIn("grade", result)
        self.assertNotIn("passed", result)

    def test_comparison_completeness_does_not_require_evergreen_to_win(self):
        from eval import peers

        manifest = peers.load_manifest(MANIFEST)
        bundles = [peer_bundle(manifest, peer["id"]) for peer in manifest["peers"]]
        self.assertTrue(peers.comparison_complete(
            manifest, bundles, "a" * 40, rows(),
        ))
        fabricated = copy.deepcopy(bundles)
        fabricated[0]["result"]["languages"]["rust"]["attempted"] = 0
        self.assertFalse(peers.comparison_complete(
            manifest, fabricated, "a" * 40, rows(),
        ))

    def test_completeness_requires_every_peer_projection_from_one_canonical_corpus(self):
        from eval import peers

        manifest = peers.load_manifest(MANIFEST)
        bundles = [peer_bundle(manifest, peer["id"]) for peer in manifest["peers"]]
        self.assertTrue(peers.comparison_complete(manifest, bundles, "a" * 40, rows()))
        disjoint = copy.deepcopy(bundles)
        drift = next(bundle for bundle in disjoint
                     if bundle["result"]["peer_id"] == "drift-guardian")
        replacement = copy.deepcopy(drift["private_rows"])
        for item in replacement:
            item["id"] = "corpus-b-" + item["id"]
        disjoint[disjoint.index(drift)] = peer_bundle(
            manifest, "drift-guardian", replacement,
        )
        self.assertFalse(peers.comparison_complete(
            manifest, disjoint, "a" * 40, rows(),
        ))

    def test_completeness_rejects_fabricated_unbound_summaries(self):
        from eval import peers

        manifest = peers.load_manifest(MANIFEST)
        summaries = [{
            "peer_id": peer["id"], "subject_commit": "a" * 40,
            "id_set_sha256": "b" * 64,
            "languages": {
                language: {"attempted": 1}
                for language, state in peer["applicability"].items()
                if state["state"] == "applicable"
            },
        } for peer in manifest["peers"]]
        self.assertFalse(peers.comparison_complete(
            manifest, summaries, "a" * 40,
            rows(),
        ))

    def test_local_peer_checkout_must_match_frozen_clean_source_identity(self):
        from eval import peers

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root,
                           check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            (root / "LICENSE").write_text("MIT fixture\n")
            (root / "package-lock.json").write_text('{"lockfileVersion":3}\n')
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root,
                                             text=True).strip()
            tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=root,
                                           text=True).strip()
            source = {
                "kind": "git", "url": "https://example.invalid/peer.git",
                "commit": commit, "tree": tree, "license": "MIT",
                "license_path": "LICENSE", "lock_path": "package-lock.json",
                "license_sha256": hashlib.sha256((root / "LICENSE").read_bytes()).hexdigest(),
                "lock_sha256": hashlib.sha256(
                    (root / "package-lock.json").read_bytes()
                ).hexdigest(),
            }
            self.assertTrue(peers.verify_git_source(source, root))
            (root / "LICENSE").write_text("changed\n")
            with self.assertRaisesRegex(peers.PeerError, "clean|hash"):
                peers.verify_git_source(source, root)

    def test_id_set_hash_is_exact_and_order_independent(self):
        from eval import peers

        expected = hashlib.sha256(
            b"".join(
                len(value).to_bytes(8, "big") + value
                for value in sorted(item["id"].encode() for item in rows())
            )
        ).hexdigest()
        self.assertEqual(peers.id_set_sha256(rows()), expected)

    def test_frozen_lane_binds_exact_peer_manifest_source_and_config(self):
        from eval.bench import frozen_run

        policy = frozen_run.peer_policy(MANIFEST, "direct-baseline", rows())
        self.assertEqual(policy["peer_id"], "direct-baseline")
        self.assertEqual(policy["peer_config_sha256"],
                         "2cf97be3532042e22a9061d985d1dfeab4f58592525980f7c0c6b80f69eaa60c")
        self.assertEqual(policy["peer_source"]["kind"], "protocol")
        self.assertRegex(policy["peer_manifest_sha256"], r"^[0-9a-f]{64}$")
        local = frozen_run.peer_policy(MANIFEST, "drift-guardian", [
            row for row in rows() if row["language"] != "rust"
        ])
        self.assertEqual(local["peer_source"]["kind"], "git")
        rust = [row for row in rows() if row["language"] == "rust"]
        with self.assertRaisesRegex(ValueError, "not applicable"):
            frozen_run.peer_policy(MANIFEST, "drift-guardian", rust)

    def test_peer_report_states_completeness_without_claiming_a_winner(self):
        from eval import peers
        from eval.bench import report

        manifest = peers.load_manifest(MANIFEST)
        bundles = [peer_bundle(manifest, peer["id"]) for peer in manifest["peers"]]
        text, complete = report.render_peer_markdown(
            manifest, bundles, "a" * 40, rows(),
        )
        self.assertTrue(complete)
        self.assertIn("Comparison completeness: **COMPLETE**", text)
        self.assertIn("direct-baseline", text)
        self.assertNotIn("winner", text.casefold())
        self.assertNotIn("best", text.casefold())
        self.assertNotIn("human", text.casefold())


if __name__ == "__main__":
    unittest.main()
