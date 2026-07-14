import copy
import json
import unittest
from pathlib import Path

from evergreen.grade import (
    GradeError,
    canonical_receipt,
    evaluate,
    load_evidence,
    load_policy,
    recompute_metrics,
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
SUBJECT = {"commit": "1" * 40, "tree": "2" * 40}
EVIDENCE_HEAD = {"commit": "3" * 40, "tree": "4" * 40}
TRUSTED_REPOSITORY = {
    "subject_ancestor_of_evidence_head": True,
    "evidence_head_is_exact": True,
}


def policy_bytes():
    return POLICY_PATH.read_bytes()


def valid_evidence():
    counts = {
        "subject_commit": SUBJECT["commit"],
        "expected_rows": 200,
        "attempted": 200,
        "provider_completed": 200,
        "decided": 200,
        "tp": 90,
        "fp": 1,
        "fn": 10,
        "tn": 99,
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
            '"attempted": 200', '"attempted": NaN', 1
        )
        with self.assertRaisesRegex(GradeError, "finite"):
            load_evidence(non_finite.encode(), self.policy)

    def test_boolean_evidence_schema_version_is_rejected(self):
        evidence = valid_evidence()
        evidence["schema_version"] = True

        with self.assertRaisesRegex(GradeError, "evidence identity"):
            self.load(evidence)

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
        self.assertEqual(metrics["specificity"], 0.99)
        self.assertAlmostEqual(metrics["f1"], 180 / 191)
        expected_matrix = {"tp": 0.09, "fp": 0.009, "fn": 0.01, "tn": 0.891}
        for name, expected in expected_matrix.items():
            self.assertAlmostEqual(metrics["prevalence_matrix"][name], expected)
        self.assertAlmostEqual(metrics["prevalence_precision"], 10 / 11)
        self.assertEqual(metrics, recompute_metrics(
            valid_evidence()["detector"]["python"], 0.10
        ))

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
            counts.update({"tp": 50, "fn": 50})
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


if __name__ == "__main__":
    unittest.main()
