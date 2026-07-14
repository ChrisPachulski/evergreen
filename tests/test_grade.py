import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest import mock

from evergreen import receipt
from evergreen.grade import (
    GradeError,
    canonical_receipt,
    evaluate,
    load_evidence,
    load_policy,
    recompute_metrics,
    verification_exit_code,
    verify_repository,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "eval" / "grade-policy-v1.json"
CATEGORIES = (
    "detector_quality",
    "same_corpus_comparison",
    "trust_security",
    "claude_self_application",
    "codex_self_application",
    "documentation_release_honesty",
    "reproducibility_ci",
    "cleanup",
)
LANGUAGES = ("go", "java", "python", "rust", "typescript")
ORACLE_KINDS = (
    "return-value", "raises", "default-value", "cardinality", "state-change",
)
SUBJECT = {"commit": "1" * 40, "tree": "2" * 40}
EVIDENCE_HEAD = {"commit": "3" * 40, "tree": "4" * 40}
TRUSTED_REPOSITORY = {
    "subject_ancestor_of_evidence_head": True,
    "evidence_head_is_exact": True,
}


def policy_bytes():
    return POLICY_PATH.read_bytes()


def valid_host_evidence(root="/candidate", home="/home"):
    hashes = {
        ".claude-plugin/plugin.json": "a" * 64,
        ".codex-plugin/plugin.json": "b" * 64,
        "bin/evergreen": "c" * 64,
        "commands/impact.md": "d" * 64,
        "skills/evergreen/SKILL.md": "e" * 64,
    }
    manifests = {
        "claude": {
            "path": f"{root}/.claude-plugin/plugin.json",
            "sha256": hashes[".claude-plugin/plugin.json"],
            "version": "0.5.0",
        },
        "codex": {
            "path": f"{root}/.codex-plugin/plugin.json",
            "sha256": hashes[".codex-plugin/plugin.json"],
            "version": "0.5.0",
        },
    }
    hosts = {}
    for name, directory, instruction in (
        ("claude", ".claude", "CLAUDE.md"),
        ("codex", ".codex", "AGENTS.md"),
    ):
        host_root = f"{home}/{directory}"
        installed = {
            "resolved_root": host_root,
            "instruction_state": "owned",
            "instruction_block_sha256": "f" * 64,
            "skill_kind": "symlink",
            "skill_target": f"{root}/skills/evergreen",
            "skill_hashes": {"skills/evergreen/SKILL.md": "e" * 64},
            "command_hashes": {
                "bin/evergreen": "c" * 64,
                "commands/impact.md": "d" * 64,
            },
            "manifest_sha256": manifests[name]["sha256"],
            "version": "0.5.0",
        }
        hosts[name] = {
            "lexical_root": host_root,
            "resolved_root": host_root,
            "resolution_chain": [{
                "path": host_root, "kind": "directory", "uid": 501, "mode": 448,
            }],
            "ownership": {
                "path": f"{host_root}/.evergreen-owned.json",
                "kind": "regular", "sha256": "1" * 64,
                "plugin_root": root, "skill_target": f"{root}/skills/evergreen",
            },
            "installed": installed,
            "doctor_issues": [],
            "discovery": copy.deepcopy(installed),
            "uninstall_owned_paths": sorted([
                f"{host_root}/{instruction}",
                f"{host_root}/.evergreen-owned.json",
                f"{host_root}/skills/evergreen",
            ]),
        }
    return {
        "schema_version": 1,
        "kind": "evergreen-host-evidence",
        "canonical": {
            "root": root, "version": "0.5.0", "hashes": hashes,
            "manifests": manifests,
        },
        "hosts": hosts,
    }


def valid_evidence():
    oracle_kinds = {
        kind: {
            "expected_rows": 70,
            "attempted": 70,
            "provider_completed": 70,
            "decided": 70,
            "tp": 18,
            "fp": 1 if index == 0 else 0,
            "fn": 2,
            "tn": 49 if index == 0 else 50,
        }
        for index, kind in enumerate(ORACLE_KINDS)
    }
    counts = {
        "subject_commit": SUBJECT["commit"],
        "expected_rows": 350,
        "attempted": 350,
        "provider_completed": 350,
        "decided": 350,
        "tp": 90,
        "fp": 1,
        "fn": 10,
        "tn": 249,
        "oracle_kinds": oracle_kinds,
    }
    results = [
        {
            "language": language,
            "subject_commit": SUBJECT["commit"],
            "id_set_sha256": "7" * 64,
        }
        for language in LANGUAGES
    ]
    return {
        "schema_version": 1,
        "kind": "evergreen-a-grade-evidence",
        "evaluated_release": "0.5.0",
        "subject": copy.deepcopy(SUBJECT),
        "policy": {"id": "a-grade-v1", "sha256": "5" * 64},
        "required_categories": list(CATEGORIES),
        "required_languages": list(LANGUAGES),
        "detector": {
            language: copy.deepcopy(counts) for language in LANGUAGES
        },
        "peers": [
            {
                "id": "direct-baseline",
                "applicability": {
                    language: "applicable" for language in LANGUAGES
                },
                "results": results,
            }
        ],
        "changed_paths": [
            "eval/grade/public/0.5.0/evidence.json",
            "eval/grade/public/0.5.0/policy.json",
            "eval/grade/public/0.5.0/report.md",
        ],
        "subject_executables": [
            {
                "path": "skills/evergreen/SKILL.md",
                "subject_sha256": "6" * 64,
                "evidence_sha256": "6" * 64,
            }
        ],
        "host_evidence": valid_host_evidence(),
        "external_states": {
            "adoption": "unverified",
            "human_review": "unverified",
            "marketplace_publication": "unverified",
        },
    }


def valid_predicates(policy):
    return {
        category: {gate: True for gate in policy["category_gates"][category]}
        for category in CATEGORIES
    }


def encode(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


def refresh_language_aggregate(counts):
    for name in (
        "expected_rows", "attempted", "provider_completed", "decided", "tp", "fp", "fn", "tn",
    ):
        counts[name] = sum(cell[name] for cell in counts["oracle_kinds"].values())


class PolicyTests(unittest.TestCase):
    def test_policy_has_closed_schema_categories_and_languages(self):
        source = json.loads(policy_bytes())
        self.assertEqual(set(source), {
            "schema_version", "kind", "policy_id", "required_categories",
            "category_gates", "required_languages", "artifact_roles", "detector",
            "required_command_ids", "forbidden_path_rules", "external_state_names",
            "limits",
        })

        policy = load_policy(policy_bytes())

        self.assertEqual(policy["required_categories"], CATEGORIES)
        self.assertEqual(policy["required_languages"], LANGUAGES)
        with self.assertRaises(TypeError):
            policy["detector"]["thresholds"]["precision"] = 0

    def test_verifier_enforces_v1_threshold_floors(self):
        source = json.loads(policy_bytes())
        source["detector"]["thresholds"]["specificity"] = 0.5

        with self.assertRaisesRegex(GradeError, "below trusted v1 floor"):
            load_policy(encode(source))

    def test_policy_declares_repository_clustered_confidence_bound_floors(self):
        detector = load_policy(policy_bytes())["detector"]

        self.assertEqual(set(detector), {
            "minimum_negative", "minimum_positive", "prevalence", "thresholds",
            "confidence_level", "confidence_cluster", "lower_bound_thresholds",
        })
        self.assertEqual(detector["confidence_level"], 0.95)
        self.assertEqual(detector["confidence_cluster"], "repository")
        self.assertEqual(detector["lower_bound_thresholds"], {
            "precision": 0.70,
            "recall": 0.70,
            "f1": 0.70,
        })

    def test_verifier_enforces_clustered_lower_bound_floors(self):
        source = json.loads(policy_bytes())
        source["detector"]["lower_bound_thresholds"] = {
            "precision": 0.70,
            "recall": 0.50,
            "f1": 0.70,
        }

        with self.assertRaisesRegex(GradeError, "lower bound recall.*trusted v1 floor"):
            load_policy(encode(source))

    def test_duplicate_keys_and_non_finite_numbers_are_rejected(self):
        with self.assertRaisesRegex(GradeError, "duplicate JSON key: kind"):
            load_policy(b'{"kind":"one","kind":"two"}')
        with self.assertRaisesRegex(GradeError, "finite"):
            load_policy(b'{"value": NaN}')

    def test_boolean_policy_schema_version_is_rejected(self):
        source = json.loads(policy_bytes())
        source["schema_version"] = True

        with self.assertRaisesRegex(GradeError, "policy identity"):
            load_policy(encode(source))

    def test_policy_required_sets_reject_null_non_lists_and_non_strings_as_grade_errors(self):
        expected = {
            "required_categories": list(CATEGORIES),
            "required_languages": list(LANGUAGES),
        }
        for field, valid in expected.items():
            for malformed in (None, 7, [*valid[:-1], 7]):
                with self.subTest(field=field, malformed=malformed):
                    source = json.loads(policy_bytes())
                    source[field] = malformed
                    try:
                        load_policy(encode(source))
                    except GradeError:
                        continue
                    except TypeError:
                        self.fail(f"{field} leaked raw TypeError")
                    self.fail(f"{field} accepted malformed list")


class EvidenceValidationTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(policy_bytes())

    def load(self, value):
        return load_evidence(encode(value), self.policy)

    def test_thresholds_come_only_from_policy(self):
        for forbidden in (
            {"thresholds": {"precision": 0}},
            {"detector_quality_threshold": 0},
        ):
            with self.subTest(forbidden=forbidden):
                evidence = valid_evidence()
                evidence.update(forbidden)
                with self.assertRaisesRegex(GradeError, "threshold override"):
                    self.load(evidence)

        loaded = self.load(valid_evidence())
        receipt = evaluate(
            self.policy, loaded, EVIDENCE_HEAD, valid_predicates(self.policy),
            TRUSTED_REPOSITORY,
        )
        self.assertEqual(
            receipt["policy"]["thresholds"], self.policy["detector"]["thresholds"]
        )

    def test_self_asserted_verdict_fields_are_rejected_at_any_depth(self):
        for field in ("grade", "passed", "success"):
            with self.subTest(field=field):
                evidence = valid_evidence()
                evidence["external_states"][field] = True
                with self.assertRaisesRegex(GradeError, "self-asserted field"):
                    self.load(evidence)

    def test_host_evidence_requires_separate_raw_hosts_and_rejects_shared_boolean(self):
        self.load(valid_evidence())

        missing = valid_evidence()
        del missing["host_evidence"]["hosts"]["codex"]
        with self.assertRaisesRegex(GradeError, "host evidence"):
            self.load(missing)

        malformed = valid_evidence()
        malformed["host_evidence"]["hosts"] = [{"claude": "not-an-object"}]
        with self.assertRaisesRegex(GradeError, "host evidence"):
            self.load(malformed)

        asserted = valid_evidence()
        asserted["host_evidence"]["hosts"]["claude"]["ok"] = True
        with self.assertRaisesRegex(GradeError, "self-asserted field: ok"):
            self.load(asserted)

    def test_manifest_cannot_contain_its_runtime_evidence_head(self):
        evidence = valid_evidence()
        evidence["evidence_head"] = copy.deepcopy(EVIDENCE_HEAD)

        with self.assertRaisesRegex(GradeError, "evidence_head"):
            self.load(evidence)

    def test_duplicate_keys_and_non_finite_evidence_are_rejected(self):
        duplicate = encode(valid_evidence()).decode().replace(
            '"schema_version": 1,', '"schema_version": 1, "schema_version": 1,', 1
        )
        with self.assertRaisesRegex(GradeError, "duplicate JSON key"):
            load_evidence(duplicate.encode(), self.policy)

        non_finite = encode(valid_evidence()).decode().replace(
            '"attempted": 350', '"attempted": NaN', 1
        )
        with self.assertRaisesRegex(GradeError, "finite"):
            load_evidence(non_finite.encode(), self.policy)

    def test_deep_json_is_rejected_by_explicit_structure_limit(self):
        nested = b'{"x":' * 80 + b"null" + b"}" * 80

        with self.assertRaisesRegex(GradeError, "JSON structure exceeds trusted limits"):
            load_evidence(nested, self.policy)

    def test_boolean_evidence_schema_version_is_rejected(self):
        evidence = valid_evidence()
        evidence["schema_version"] = True

        with self.assertRaisesRegex(GradeError, "evidence identity"):
            self.load(evidence)

    def test_evidence_required_sets_reject_null_non_lists_and_non_strings_as_grade_errors(self):
        expected = {
            "required_categories": list(CATEGORIES),
            "required_languages": list(LANGUAGES),
        }
        for field, valid in expected.items():
            for malformed in (None, 7, [*valid[:-1], 7]):
                with self.subTest(field=field, malformed=malformed):
                    evidence = valid_evidence()
                    evidence[field] = malformed
                    try:
                        self.load(evidence)
                    except GradeError:
                        continue
                    except TypeError:
                        self.fail(f"{field} leaked raw TypeError")
                    self.fail(f"{field} accepted malformed list")

    def test_categories_and_languages_must_be_exact(self):
        cases = []
        missing_category = valid_evidence()
        missing_category["required_categories"].pop()
        cases.append((missing_category, "categories"))
        unknown_category = valid_evidence()
        unknown_category["required_categories"][-1] = "invented"
        cases.append((unknown_category, "categories"))
        missing_language = valid_evidence()
        del missing_language["detector"]["go"]
        cases.append((missing_language, "languages"))
        partial_language_declaration = valid_evidence()
        partial_language_declaration["required_languages"].pop()
        cases.append((partial_language_declaration, "languages"))

        for evidence, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(GradeError, message):
                    self.load(evidence)

    def test_dropped_rows_stale_commits_and_incomplete_peer_matrix_fail_closed(self):
        dropped = valid_evidence()
        dropped["detector"]["python"]["attempted"] = 199
        stale = valid_evidence()
        stale["detector"]["java"]["subject_commit"] = "9" * 40
        incomplete = valid_evidence()
        del incomplete["peers"][0]["applicability"]["rust"]
        missing_result = valid_evidence()
        missing_result["peers"][0]["results"].pop()

        for evidence, message in (
            (dropped, "dropped rows"),
            (stale, "stale subject commit"),
            (incomplete, "peer applicability"),
            (missing_result, "peer results"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(GradeError, message):
                    self.load(evidence)

    def test_oracle_kind_cells_are_closed_complete_bounded_and_match_aggregate(self):
        complete = valid_evidence()
        self.load(complete)

        missing = valid_evidence()
        del missing["detector"]["python"]["oracle_kinds"]["raises"]
        too_small = valid_evidence()
        cell = too_small["detector"]["rust"]["oracle_kinds"]["return-value"]
        cell.update({"fp": 0, "tn": 39, "expected_rows": 59, "attempted": 59,
                     "provider_completed": 59, "decided": 59})
        refresh_language_aggregate(too_small["detector"]["rust"])
        contradictory = valid_evidence()
        contradictory["detector"]["go"]["fp"] += 1
        contradictory["detector"]["go"]["tn"] -= 1

        for evidence, message in (
            (missing, "oracle kinds"),
            (too_small, "oracle kind.*too few negative"),
            (contradictory, "oracle kind counts do not match aggregate"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(GradeError, message):
                    self.load(evidence)

    def test_subject_and_evidence_head_are_distinct_and_only_evidence_paths_may_change(self):
        loaded = self.load(valid_evidence())
        predicates = valid_predicates(self.policy)

        receipt = evaluate(
            self.policy, loaded, EVIDENCE_HEAD, predicates, TRUSTED_REPOSITORY
        )

        self.assertEqual(receipt["subject"], SUBJECT)
        self.assertEqual(receipt["evidence_head"], EVIDENCE_HEAD)

        with self.assertRaisesRegex(GradeError, "later than subject"):
            evaluate(self.policy, loaded, SUBJECT, predicates)
        same_commit = {"commit": SUBJECT["commit"], "tree": "9" * 40}
        with self.assertRaisesRegex(GradeError, "later than subject"):
            evaluate(self.policy, loaded, same_commit, predicates)

        changed_code = valid_evidence()
        changed_code["changed_paths"].append("evergreen/grade.py")
        with self.assertRaisesRegex(GradeError, "canonical release evidence path"):
            self.load(changed_code)

        changed_executable = valid_evidence()
        changed_executable["subject_executables"][0]["evidence_sha256"] = "8" * 64
        with self.assertRaisesRegex(GradeError, "executable changed"):
            self.load(changed_executable)

    def test_only_canonical_release_evidence_paths_may_change(self):
        noncanonical = valid_evidence()
        noncanonical["changed_paths"] = [
            "eval/grade-evidence/results.json",
            "eval/grade-reports/a-grade.json",
        ]
        with self.assertRaisesRegex(GradeError, "canonical release evidence path"):
            self.load(noncanonical)

        canonical = valid_evidence()
        canonical["changed_paths"] = [
            "eval/grade/public/0.5.0/evidence.json",
            "eval/grade/public/0.5.0/policy.json",
            "eval/grade/public/0.5.0/report.md",
        ]
        self.load(canonical)


class GradeTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(policy_bytes())
        self.evidence = load_evidence(encode(valid_evidence()), self.policy)

    def test_raw_counts_recompute_metrics_and_ten_percent_prevalence(self):
        metrics = recompute_metrics(valid_evidence()["detector"]["python"], 0.10)

        self.assertEqual(metrics["provider_completion"], 1.0)
        self.assertEqual(metrics["semantic_coverage"], 1.0)
        self.assertEqual(metrics["precision"], 90 / 91)
        self.assertEqual(metrics["recall"], 0.9)
        self.assertEqual(metrics["specificity"], 249 / 250)
        self.assertAlmostEqual(metrics["f1"], 180 / 191)
        expected_matrix = {"tp": 0.09, "fp": 0.0036, "fn": 0.01, "tn": 0.8964}
        for name, expected in expected_matrix.items():
            self.assertAlmostEqual(metrics["prevalence_matrix"][name], expected)
        self.assertAlmostEqual(metrics["prevalence_precision"], 25 / 26)
        self.assertEqual(metrics, recompute_metrics(
            valid_evidence()["detector"]["python"], 0.10
        ))

    def test_zero_predicted_positives_return_zero_adjusted_precision_and_not_earned(self):
        counts = valid_evidence()["detector"]["python"]
        counts.update({"tp": 0, "fp": 0, "fn": 100, "tn": 250})
        try:
            metrics = recompute_metrics(counts, 0.10)
        except ZeroDivisionError:
            self.fail("zero predicted positives must not raise ZeroDivisionError")
        self.assertEqual(metrics["prevalence_precision"], 0.0)
        self.assertEqual(metrics["prevalence_f1"], 0.0)

        evidence = valid_evidence()
        for language_counts in evidence["detector"].values():
            for cell in language_counts["oracle_kinds"].values():
                cell.update({"tp": 0, "fp": 0, "fn": 20, "tn": 50})
            refresh_language_aggregate(language_counts)
        receipt = evaluate(
            self.policy,
            load_evidence(encode(evidence), self.policy),
            EVIDENCE_HEAD,
            valid_predicates(self.policy),
            TRUSTED_REPOSITORY,
        )
        detector = next(
            item for item in receipt["categories"] if item["id"] == "detector_quality"
        )
        self.assertEqual(detector["status"], "not-earned")
        self.assertIn("detector:go:precision", detector["reasons"])

    def test_counts_only_evidence_cannot_earn_without_clustered_confidence_bounds(self):
        receipt = evaluate(
            self.policy,
            self.evidence,
            EVIDENCE_HEAD,
            valid_predicates(self.policy),
            TRUSTED_REPOSITORY,
        )

        detector = next(
            item for item in receipt["categories"] if item["id"] == "detector_quality"
        )
        self.assertEqual(detector["status"], "not-earned")
        self.assertIn("detector:repository-clustered-bounds-missing", detector["reasons"])
        self.assertIsNone(receipt["grade"])

    def test_trusted_ancestry_and_exact_head_observations_are_required_to_earn(self):
        predicates = valid_predicates(self.policy)
        with self.assertRaisesRegex(GradeError, "trusted repository observation"):
            evaluate(self.policy, self.evidence, EVIDENCE_HEAD, predicates)

        receipt = evaluate(
            self.policy,
            self.evidence,
            EVIDENCE_HEAD,
            predicates,
            {
                "subject_ancestor_of_evidence_head": False,
                "evidence_head_is_exact": True,
            },
        )

        reproducibility = next(
            item for item in receipt["categories"] if item["id"] == "reproducibility_ci"
        )
        self.assertEqual(reproducibility["status"], "not-earned")
        self.assertIn("repository:subject-not-ancestor", reproducibility["reasons"])
        self.assertIsNone(receipt["grade"])

        receipt = evaluate(
            self.policy,
            self.evidence,
            EVIDENCE_HEAD,
            predicates,
            {
                "subject_ancestor_of_evidence_head": True,
                "evidence_head_is_exact": False,
            },
        )
        reproducibility = next(
            item for item in receipt["categories"] if item["id"] == "reproducibility_ci"
        )
        self.assertEqual(reproducibility["status"], "not-earned")
        self.assertIn("repository:evidence-head-not-exact", reproducibility["reasons"])

    def test_one_failed_predicate_fails_only_its_category_and_overall_grade(self):
        predicates = valid_predicates(self.policy)
        baseline = evaluate(
            self.policy, self.evidence, EVIDENCE_HEAD, predicates, TRUSTED_REPOSITORY
        )
        predicates["cleanup"]["clean_tree"] = False

        receipt = evaluate(
            self.policy, self.evidence, EVIDENCE_HEAD, predicates, TRUSTED_REPOSITORY
        )

        statuses = {item["id"]: item["status"] for item in receipt["categories"]}
        baseline_statuses = {
            item["id"]: item["status"] for item in baseline["categories"]
        }
        self.assertEqual(statuses["cleanup"], "not-earned")
        self.assertEqual(baseline_statuses["cleanup"], "earned")
        self.assertEqual(
            {category: status for category, status in statuses.items() if category != "cleanup"},
            {
                category: status for category, status in baseline_statuses.items()
                if category != "cleanup"
            },
        )
        self.assertEqual(receipt["status"], "not-earned")
        self.assertIsNone(receipt["grade"])

    def test_failed_derived_gate_cannot_be_overridden_by_true_predicate(self):
        evidence = valid_evidence()
        for counts in evidence["detector"].values():
            for cell in counts["oracle_kinds"].values():
                cell.update({"tp": 10, "fn": 10})
            refresh_language_aggregate(counts)
        loaded = load_evidence(encode(evidence), self.policy)

        receipt = evaluate(
            self.policy, loaded, EVIDENCE_HEAD, valid_predicates(self.policy),
            TRUSTED_REPOSITORY,
        )

        detector = next(
            item for item in receipt["categories"] if item["id"] == "detector_quality"
        )
        self.assertEqual(detector["status"], "not-earned")
        self.assertIn("detector:go:recall", detector["reasons"])

    def test_oracle_kind_thresholds_are_recomputed_independently(self):
        evidence = valid_evidence()
        counts = evidence["detector"]["go"]
        counts["oracle_kinds"]["return-value"].update({"tp": 10, "fn": 10})
        counts["oracle_kinds"]["raises"].update({"tp": 20, "fn": 0})
        refresh_language_aggregate(counts)
        loaded = load_evidence(encode(evidence), self.policy)

        receipt = evaluate(
            self.policy, loaded, EVIDENCE_HEAD, valid_predicates(self.policy),
            TRUSTED_REPOSITORY,
        )

        detector = next(
            item for item in receipt["categories"] if item["id"] == "detector_quality"
        )
        self.assertIn("detector:go:return-value:recall", detector["reasons"])
        self.assertNotIn("detector:go:recall", detector["reasons"])
        self.assertEqual(
            receipt["detector_oracle_kind_metrics"]["go"]["return-value"]["recall"],
            0.5,
        )

    def test_external_states_are_ungraded(self):
        predicates = valid_predicates(self.policy)
        first = evaluate(
            self.policy, self.evidence, EVIDENCE_HEAD, predicates, TRUSTED_REPOSITORY
        )
        changed = valid_evidence()
        changed["external_states"] = {
            "adoption": "verified",
            "human_review": "not-applicable",
            "marketplace_publication": "verified",
        }
        second = evaluate(
            self.policy,
            load_evidence(encode(changed), self.policy),
            EVIDENCE_HEAD,
            predicates,
            TRUSTED_REPOSITORY,
        )

        self.assertEqual(first["grade"], second["grade"])
        self.assertEqual(first["status"], second["status"])
        self.assertEqual(first["categories"], second["categories"])
        self.assertNotEqual(first["external_states"], second["external_states"])

    def test_receipt_serialization_is_byte_deterministic_and_runtime_only(self):
        predicates = valid_predicates(self.policy)
        first = evaluate(
            self.policy, self.evidence, EVIDENCE_HEAD, predicates, TRUSTED_REPOSITORY
        )
        second = evaluate(
            self.policy, self.evidence, EVIDENCE_HEAD, predicates, TRUSTED_REPOSITORY
        )

        self.assertEqual(canonical_receipt(first), canonical_receipt(second))
        manifest_bytes = encode(valid_evidence())
        self.assertNotIn(EVIDENCE_HEAD["commit"].encode(), manifest_bytes)
        self.assertIn(EVIDENCE_HEAD["commit"].encode(), canonical_receipt(first))


class TrustedRepositoryVerificationTests(unittest.TestCase):
    def test_exit_codes_are_derived_only_from_the_result(self):
        self.assertEqual(verification_exit_code({"status": "earned", "grade": "A"}), 0)
        self.assertEqual(
            verification_exit_code({"status": "not-earned", "grade": None}), 2
        )
        self.assertEqual(verification_exit_code({"status": "invalid", "grade": None}), 2)
        self.assertEqual(
            verification_exit_code({"status": "inconclusive", "grade": None}), 1
        )
        self.assertEqual(verification_exit_code({"status": "earned", "grade": None}), 2)

    def test_path_swap_between_snapshots_is_an_operational_failure(self):
        verifier = {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "artifact_sha256": "c" * 64,
        }
        clean = {
            "schema_version": 1,
            "repository": {
                "root": "/repo", "head": "d" * 40, "clean": True,
            },
        }
        changed = copy.deepcopy(clean)
        changed["repository"]["head"] = "e" * 40

        with (
            mock.patch("evergreen.grade._trusted_verifier_identity", return_value=verifier),
            mock.patch(
                "evergreen.grade.receipt.build_receipt", side_effect=[clean, changed]
            ),
            mock.patch("evergreen.grade._verify_snapshot", return_value={"status": "not-earned"}),
        ):
            result = verify_repository(Path("/repo"), "evidence.json", Path("/verifier"))

        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["failures"][0]["code"], "repository-changed")
        self.assertEqual(result["verifier"], verifier)

    def test_bounded_candidate_git_failure_is_inconclusive(self):
        verifier = {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "artifact_sha256": "c" * 64,
        }
        with (
            mock.patch("evergreen.grade._trusted_verifier_identity", return_value=verifier),
            mock.patch(
                "evergreen.grade.receipt.build_receipt",
                side_effect=receipt.ReceiptOperationalError("Git command timed out"),
            ),
        ):
            result = verify_repository(Path("/repo"), "evidence.json", Path("/verifier"))

        self.assertEqual(verification_exit_code(result), 1)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["failures"], [{
            "code": "operational-error", "detail": "Git command timed out",
        }])
        self.assertEqual(result["verifier"], verifier)

    def test_verifier_uses_no_direct_process_network_or_mutating_facility(self):
        source = (ROOT / "evergreen" / "grade.py").read_text()

        for forbidden in (
            "import subprocess", "from subprocess", "import socket", "urllib.request",
            "provider_completed(", "install(", "uninstall(", "TransactionEngine",
            "open(\"w", "write_text(", "write_bytes(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_verifier_identity_artifact_hash_is_not_a_candidate_boolean(self):
        identity = {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "artifact_sha256": hashlib.sha256(b"trusted verifier").hexdigest(),
        }
        self.assertEqual(set(identity), {"commit", "tree", "artifact_sha256"})


if __name__ == "__main__":
    unittest.main()
