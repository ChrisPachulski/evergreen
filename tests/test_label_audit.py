import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import dataclasses
from pathlib import Path

from eval.bench import label_audit_core as core


def result_row(identifier, language="Python", label="consistent", status="complete",
               verdict="consistent"):
    return {
        "id": identifier,
        "func": "f",
        "code": "def f():\n    return 1",
        "doc": "Returns one.",
        "label": label,
        "category": None,
        "language": language,
        "got": {"final_status": status, "final_verdict": verdict},
    }


class LabelAuditInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def artifact(self, rows, name="artifact.json"):
        path = self.root / name
        path.write_text(json.dumps({
            "schema_version": 2,
            "metadata": {"dataset": {"sha256": "d" * 64}},
            "rows": rows,
            "timing": {},
        }))
        return path

    def test_load_artifact_normalizes_language_and_binds_hash(self):
        path = self.artifact([
            result_row("a", label="inconsistent", verdict="inconsistent"),
            result_row("b"),
        ])
        loaded = core.load_artifact(path)
        self.assertEqual(loaded.language, "python")
        self.assertEqual(loaded.row_count, 2)
        self.assertEqual(loaded.sha256, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(loaded.items[0].key, ("python", "a"))

    def test_load_artifact_rejects_duplicates_mixed_languages_and_bad_results(self):
        cases = (
            ([result_row("a"), result_row("a")], "duplicate"),
            ([result_row("a"), result_row("b", language="Go")], "one language"),
            ([{k: v for k, v in result_row("a").items() if k != "got"}], "result"),
            ([result_row("a", status="complete", verdict=None)], "verdict"),
            ([result_row("a", status="abstain", verdict="consistent")], "abstain"),
        )
        for rows, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                core.load_artifact(self.artifact(rows))

    def test_load_artifact_rejects_empty_and_symlink(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            core.load_artifact(self.artifact([]))
        target = self.artifact([result_row("a")], "target.json")
        link = self.root / "link.json"
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "regular file"):
            core.load_artifact(link)

    def test_source_pool_records_incomplete_provenance(self):
        path = self.root / "source.jsonl"
        path.write_text(json.dumps({
            **result_row("a#0-old", language="typescript", label="inconsistent",
                         verdict="inconsistent"),
            "source": None,
        }) + "\n")
        loaded = core.load_source_pool(path, "typescript")
        self.assertEqual(loaded.provenance_status, "unverified")
        self.assertEqual(loaded.rows[0]["source_status"], "missing")

    def test_canonical_language_is_closed(self):
        self.assertEqual(core.canonical_language("TS"), "typescript")
        self.assertEqual(core.canonical_language("Java"), "java")
        with self.assertRaisesRegex(ValueError, "language"):
            core.canonical_language("ruby")


def audit_item(identifier, language, label="consistent", verdict="consistent", status="complete"):
    return core.AuditItem(identifier, language, "line one\nline two", "A doc claim.", "f",
                          label, None, status, verdict, "a" * 64)


def artifact_input(language, items):
    return core.ArtifactInput(Path(f"/{language}.json"), "a" * 64, language, len(items),
                              "d" * 64, tuple(items))


class LabelAuditSamplingTests(unittest.TestCase):
    def synthetic_artifacts(self):
        artifacts = []
        for language in core.LANGUAGES:
            rows = [
                audit_item(f"owner/{language}/positive#0-old", language, "inconsistent", "inconsistent"),
                audit_item(f"owner/{language}/fp#1-new", language, "consistent", "inconsistent"),
            ]
            rows += [audit_item(f"owner/{language}/tn-{i}#2-new", language) for i in range(30)]
            if language == "rust":
                rows.append(audit_item("owner/rust/abstain#3-new", language, status="abstain", verdict=None))
            artifacts.append(artifact_input(language, rows))
        return tuple(artifacts)

    def test_build_sample_censuses_risk_and_samples_25_tn_per_language(self):
        selection = core.build_sample(self.synthetic_artifacts(), {}, audit_id="audit-1")
        self.assertEqual(selection.count("nominal_positive"), 5)
        self.assertEqual(selection.count("nominal_false_positive"), 5)
        self.assertEqual(selection.count("true_negative_sample"), 125)
        self.assertEqual(selection.count("abstention"), 1)
        self.assertEqual(selection.missing_discarded_languages,
                         ("go", "python", "rust", "typescript"))
        reversed_selection = core.build_sample(tuple(reversed(self.synthetic_artifacts())), {},
                                               audit_id="audit-1")
        self.assertEqual([r.item.key for r in selection.selected],
                         [r.item.key for r in reversed_selection.selected])

    def test_packets_are_opaque_private_and_differently_ordered(self):
        selection = core.build_sample(self.synthetic_artifacts(), {}, audit_id="audit-1")
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "packets"
            outputs = core.write_blinded_packets(
                selection, work, blind_key=b"k" * 32, rubric_sha256="f" * 64,
                repo=Path.cwd(),
            )
            a = json.loads(outputs.annotator_a.read_text())
            b = json.loads(outputs.annotator_b.read_text())
            serialized = json.dumps(a)
            for forbidden in ('"label"', "final_verdict", "stratum", "#0-old", "#1-new", "votes"):
                self.assertNotIn(forbidden, serialized)
            self.assertRegex(a["items"][0]["blind_id"], r"^item-[0-9a-f]{24}$")
            self.assertEqual({x["blind_id"] for x in a["items"]},
                             {x["blind_id"] for x in b["items"]})
            self.assertNotEqual([x["blind_id"] for x in a["items"]],
                                [x["blind_id"] for x in b["items"]])
            self.assertEqual(stat.S_IMODE(outputs.coordinator.stat().st_mode), 0o600)

    def test_packet_writer_refuses_short_key_relative_and_repo_output(self):
        selection = core.build_sample(self.synthetic_artifacts(), {}, audit_id="audit-1")
        with self.assertRaisesRegex(ValueError, "32 bytes"):
            core.write_blinded_packets(selection, Path("/tmp/audit-short"), blind_key=b"x",
                                       rubric_sha256="f" * 64, repo=Path.cwd())
        with self.assertRaisesRegex(ValueError, "absolute"):
            core.write_blinded_packets(selection, Path("relative"), blind_key=b"k" * 32,
                                       rubric_sha256="f" * 64, repo=Path.cwd())
        with self.assertRaisesRegex(ValueError, "outside"):
            core.write_blinded_packets(selection, Path.cwd() / "packets", blind_key=b"k" * 32,
                                       rubric_sha256="f" * 64, repo=Path.cwd())

    def test_source_pool_cannot_clear_missing_without_manifest_admission(self):
        artifacts = self.synthetic_artifacts()
        invented = core.SourcePool(Path("/invented.jsonl"), "e" * 64, "python", 20,
                                   "complete", tuple())
        with self.assertRaisesRegex(ValueError, "manifest"):
            core.build_sample(artifacts, {"python": invented}, audit_id="audit-1")


class LabelAuditAnnotationTests(unittest.TestCase):
    def judgment(self, blind_id="item-a", verdict="consistent"):
        return {
            "blind_id": blind_id,
            "verdict": verdict,
            "category": "direct-mismatch" if verdict == "inconsistent" else None,
            "documentation_claim": "The documentation promises one.",
            "code_evidence": "Line 2 returns one.",
            "rationale": "The evidence settles the claim.",
            "missing_context": "A callee is absent." if verdict == "insufficient-context" else None,
        }

    def test_validate_judgment_enforces_cross_field_evidence(self):
        core.validate_judgment(self.judgment(verdict="inconsistent"))
        for field, value in (("category", None), ("documentation_claim", ""),
                             ("code_evidence", ""), ("rationale", "")):
            broken = {**self.judgment(verdict="inconsistent"), field: value}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                core.validate_judgment(broken)
        with self.assertRaisesRegex(ValueError, "missing_context"):
            core.validate_judgment({**self.judgment(verdict="insufficient-context"),
                                    "missing_context": ""})

    def test_third_review_is_disagreements_uncertainties_and_10_percent_agreements(self):
        first = core.AnnotationSet("audit", "f" * 64, "H1", "self-attested-human", False,
                                   tuple(self.judgment(f"item-{i}") for i in range(20)))
        second_rows = [self.judgment(f"item-{i}") for i in range(20)]
        second_rows[0] = self.judgment("item-0", "inconsistent")
        second_rows[1] = self.judgment("item-1", "insufficient-context")
        second = core.AnnotationSet("audit", "f" * 64, "H2", "self-attested-human", False,
                                    tuple(second_rows))
        selected = core.select_third_review(first, second, rate=0.10, seed=7)
        self.assertIn("item-0", selected)
        self.assertIn("item-1", selected)
        self.assertEqual(len(selected), 4)  # two mandatory + ceil(10% of 18 agreements)

    def test_category_disagreement_requires_third_review(self):
        direct = self.judgment("item-a", "inconsistent")
        over = {**direct, "category": "over-promise"}
        first = core.AnnotationSet("audit", "f" * 64, "H1", "self-attested-human", False,
                                   (direct,))
        second = core.AnnotationSet("audit", "f" * 64, "H2", "self-attested-human", False,
                                    (over,))
        self.assertEqual(core.select_third_review(first, second, rate=0, seed=1), ("item-a",))

    def test_load_annotations_requires_exact_packet_and_self_attestation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packet = root / "packet.json"
            labels = root / "labels.json"
            packet_document = {"schema_version": 1, "audit_id": "audit",
                               "rubric_sha256": "f" * 64,
                               "coordinator_sha256": "c" * 64,
                               "items": [{"blind_id": "item-a", "language": "python",
                                          "code": "1 | return 1",
                                          "documentation": "1 | Returns one."}]}
            packet_document["packet_sha256"] = core.document_identity(
                packet_document, "packet_sha256")
            packet.write_text(json.dumps(packet_document))
            labels.write_text(json.dumps({
                "schema_version": 1, "audit_id": "audit", "rubric_sha256": "f" * 64,
                "coordinator_sha256": "c" * 64,
                "packet_sha256": packet_document["packet_sha256"],
                "annotator": {"annotator_id": "H1", "human_judgment": True,
                              "worked_independently": True, "used_model_assistance": False},
                "judgments": [self.judgment()],
            }))
            loaded = core.load_annotations(labels, packet)
            self.assertEqual(loaded.trust_status, "self-attested-human")
            self.assertFalse(loaded.humanity_verified)
            document = json.loads(labels.read_text())
            document["judgments"] = []
            labels.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "coverage"):
                core.load_annotations(labels, packet)
            document["judgments"] = [self.judgment()]
            document["unexpected"] = True
            labels.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "fields"):
                core.load_annotations(labels, packet)

    def test_combine_requires_two_matching_decisive_votes(self):
        first = core.AnnotationSet("audit", "f" * 64, "H1", "self-attested-human", False,
                                   (self.judgment(),))
        second = core.AnnotationSet("audit", "f" * 64, "H2", "self-attested-human", False,
                                    (self.judgment(verdict="inconsistent"),))
        third = core.AnnotationSet("audit", "f" * 64, "H3", "self-attested-human", False,
                                   (self.judgment(),))
        combined = core.combine_human_labels(first, second, third, ("item-a",))
        self.assertEqual(combined.labels[0].final_verdict, "consistent")
        unresolved = core.combine_human_labels(
            first, second,
            core.AnnotationSet("audit", "f" * 64, "H3", "self-attested-human", False,
                               (self.judgment(verdict="insufficient-context"),)),
            ("item-a",),
        )
        self.assertTrue(unresolved.labels[0].unresolved)

    def test_schema_is_closed_at_every_object_level(self):
        schema = json.loads(Path("eval/bench/human-label.schema.json").read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["properties"]["annotator"]["additionalProperties"])
        judgment = schema["properties"]["judgments"]["items"]
        self.assertFalse(judgment["additionalProperties"])


class LabelAuditStatisticsTests(unittest.TestCase):
    def test_known_kappa_wilson_and_weighted_error(self):
        from eval.bench import label_audit_stats as stats
        a = ["consistent", "consistent", "inconsistent", "inconsistent"]
        b = ["consistent", "inconsistent", "inconsistent", "inconsistent"]
        self.assertAlmostEqual(stats.cohen_kappa(a, b), 0.5)
        estimate = stats.weighted_error([
            stats.AuditResult("python", "census", True, 1.0),
            stats.AuditResult("python", "sample", False, 0.25),
        ])
        self.assertAlmostEqual(estimate.point, 0.2)
        self.assertLessEqual(estimate.lower, estimate.point)
        self.assertGreater(estimate.upper, estimate.point)
        zero = stats.weighted_error([
            stats.AuditResult("python", "sample", False, 1.0) for _ in range(25)
        ])
        self.assertGreater(zero.upper, 0.13)
        self.assertLess(stats.wilson_interval(0, 25)[1], 0.14)

    def test_gate_pass_unverified_and_escalate(self):
        from eval.bench import label_audit_stats as stats
        base = stats.GateInputs(
            overall_kappa=0.8, overall_kappa_lower=0.7,
            language_kappa={language: 0.7 for language in core.LANGUAGES},
            overall_error_upper=0.04,
            language_error_upper={language: 0.09 for language in core.LANGUAGES},
            max_census_error=0.04, discarded_usable_rate=0.04,
            unresolved_count=0, missing_discarded_languages=(), census_complete=False,
        )
        self.assertEqual(stats.evaluate_gate(base).status, "pass")
        self.assertEqual(stats.evaluate_gate(
            dataclasses.replace(base, missing_discarded_languages=("go",))).status,
                         "unverified")
        failed = stats.evaluate_gate(dataclasses.replace(base, overall_kappa=0.69))
        self.assertEqual(failed.status, "escalate")
        self.assertIn("overall kappa", failed.reasons)
        with self.assertRaisesRegex(ValueError, "unresolved"):
            stats.evaluate_gate(dataclasses.replace(base, unresolved_count=1))
        undefined = stats.evaluate_gate(dataclasses.replace(
            base, language_kappa={**base.language_kappa, "go": None}))
        self.assertEqual(undefined.status, "escalate")
        self.assertIn("go kappa", undefined.reasons)
        overall_undefined = stats.evaluate_gate(dataclasses.replace(base, overall_kappa=None))
        self.assertEqual(overall_undefined.status, "escalate")
        self.assertIn("overall kappa", overall_undefined.reasons)

    def test_report_renders_calculated_census_and_discarded_rates(self):
        from eval.bench import label_audit_stats as stats
        inputs = stats.GateInputs(
            0.8, 0.7, {language: 0.75 for language in core.LANGUAGES}, 0.04,
            {language: 0.08 for language in core.LANGUAGES}, 0.031, 0.042, 0, (), False)
        evidence = {
            "coordinator_sha256": "c" * 64, "selected_count": 10,
            "third_review_rate": 0.1, "uncertainty_rate": 0.02,
            "input_hashes": {"python": "a" * 64},
            "language_error": {language: {"point": 0.01, "lower": 0.0, "upper": 0.08}
                               for language in core.LANGUAGES},
            "language_kappa": {language: 0.75 for language in core.LANGUAGES},
            "counts_by_language_stratum": {"python:true_negative_sample": 10},
            "inclusion_probabilities": {"python:true_negative_sample": [0.5]},
            "max_census_error": 0.031, "discarded_usable_rate": 0.042,
            "thresholds": {"max_census_error": 0.05, "discarded_usable_rate": 0.05},
        }
        report = stats.render_audit_report(inputs, stats.evaluate_gate(inputs), evidence=evidence)
        self.assertIn("Maximum census-stratum error: `0.031`", report)
        self.assertIn("Discarded-candidate usable rate: `0.042`", report)

    def test_analysis_json_evidence_contains_calculated_gate_rates(self):
        from eval.bench import label_audit
        coordinator_rows, first_rows, second_rows, final_rows = [], [], [], []
        for language in core.LANGUAGES:
            for suffix, verdict, label, stratum in (
                    ("c", "consistent", "consistent", "true_negative_sample"),
                    ("i", "inconsistent", "consistent" if language == "java" else "inconsistent",
                     "nominal_false_positive")):
                blind = f"{language}-{suffix}"
                judgment = LabelAuditAnnotationTests().judgment(blind, verdict)
                first_rows.append(judgment); second_rows.append(dict(judgment))
                final_rows.append(core.CombinedLabel(
                    blind, verdict, judgment["category"], False, None, (judgment, judgment)))
                coordinator_rows.append({"blind_id": blind, "language": language,
                                         "stratum": stratum, "label": label,
                                         "inclusion_probability": 1.0})
        judgment = LabelAuditAnnotationTests().judgment("python-discarded", "inconsistent")
        first_rows.append(judgment); second_rows.append(dict(judgment))
        final_rows.append(core.CombinedLabel("python-discarded", "inconsistent",
                                             "direct-mismatch", False, None,
                                             (judgment, judgment)))
        coordinator_rows.append({"blind_id": "python-discarded", "language": "python",
                                 "stratum": "discarded_candidate", "label": "consistent",
                                 "inclusion_probability": 1.0})
        first = core.AnnotationSet("audit", "f" * 64, "H1", "self-attested-human", False,
                                   tuple(first_rows))
        second = core.AnnotationSet("audit", "f" * 64, "H2", "self-attested-human", False,
                                    tuple(second_rows))
        combined = core.CombinedLabels("audit", "f" * 64, tuple(final_rows))
        coordinator = {"coordinator_sha256": "c" * 64,
                       "input_hashes": {language: "a" * 64 for language in core.LANGUAGES},
                       "missing_discarded_languages": (), "items": coordinator_rows}
        inputs, _, evidence = label_audit._analysis(coordinator, first, second, combined)
        self.assertEqual(evidence["max_census_error"], inputs.max_census_error)
        self.assertEqual(evidence["discarded_usable_rate"], inputs.discarded_usable_rate)
        self.assertEqual(evidence["max_census_error"], 1.0)
        self.assertEqual(evidence["discarded_usable_rate"], 1.0)


class LabelAuditOverlayTests(unittest.TestCase):
    def label_package(self, artifact, labels):
        rows = []
        for item in artifact.items:
            if item.key not in labels:
                continue
            verdict, category = labels[item.key]
            rows.append({"blind_id": f"blind-{item.id}", "id": item.id,
                         "language": item.language, "item_sha256": core.item_sha256(item),
                         "human_verdict": verdict, "human_category": category})
        package = {"schema_version": 1, "audit_id": "audit", "rubric_sha256": "f" * 64,
                   "coordinator_sha256": "c" * 64,
                   "gate": {"status": "unverified", "qualification": None, "reasons": []},
                   "evidence": {"coordinator_sha256": "c" * 64}, "labels": rows}
        package["label_package_sha256"] = core.document_identity(
            package, "label_package_sha256")
        return package

    def bound_package(self, artifact, labels, root):
        rubric = root / "rubric.md"
        rubric.write_text("Frozen rubric\n")
        rubric_sha256 = core.sha256_file(rubric)
        package = self.label_package(artifact, labels)
        items = []
        by_key = {item.key: item for item in artifact.items}
        for row in package["labels"]:
            item = by_key[(row["language"], row["id"])]
            items.append({"blind_id": row["blind_id"], "id": row["id"],
                          "language": row["language"], "code": item.code, "doc": item.doc})
        coordinator = {"audit_id": "audit", "rubric_sha256": rubric_sha256, "items": items}
        coordinator["coordinator_sha256"] = core.document_identity(
            coordinator, "coordinator_sha256")
        package["coordinator_sha256"] = coordinator["coordinator_sha256"]
        package["evidence"]["coordinator_sha256"] = coordinator["coordinator_sha256"]
        package["rubric_sha256"] = rubric_sha256
        package["label_package_sha256"] = core.document_identity(
            package, "label_package_sha256")
        return package, coordinator, rubric

    def test_rescore_requires_complete_content_bound_overlay(self):
        item_a = audit_item("owner/repo/a", "python", "inconsistent", "consistent")
        item_b = audit_item("owner/repo/b", "python", "consistent", "consistent")
        artifact = artifact_input("python", [item_a, item_b])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package, coordinator, rubric = self.bound_package(artifact, {
                item_a.key: ("inconsistent", "direct-mismatch"),
            }, root)
            partial = core.build_overlay(artifact, package)
            with self.assertRaisesRegex(ValueError, "100%"):
                core.rescore_overlay(artifact, partial, label_package=package,
                                     coordinator=coordinator, rubric=rubric)
            package, coordinator, rubric = self.bound_package(artifact, {
                item_a.key: ("consistent", None), item_b.key: ("consistent", None),
            }, root)
            complete = core.build_overlay(artifact, package)
            result = core.rescore_overlay(artifact, complete, label_package=package,
                                          coordinator=coordinator, rubric=rubric)
            self.assertEqual(result["corrected"]["tn"], 2)
            self.assertEqual(result["corrected"]["tp"], 0)

    def test_rescore_rejects_forged_self_hashed_overlay(self):
        item = audit_item("owner/repo/a", "python")
        artifact = artifact_input("python", [item])
        with tempfile.TemporaryDirectory() as directory:
            package, coordinator, rubric = self.bound_package(
                artifact, {item.key: ("consistent", None)}, Path(directory))
            overlay = core.build_overlay(artifact, package)
            overlay["rows"][0]["human_verdict"] = "inconsistent"
            overlay["rows"][0]["human_category"] = "direct-mismatch"
            overlay["overlay_sha256"] = core.document_identity(overlay, "overlay_sha256")
            with self.assertRaisesRegex(ValueError, "derivation"):
                core.rescore_overlay(artifact, overlay, label_package=package,
                                     coordinator=coordinator, rubric=rubric)

    def test_overlay_rejects_tampered_label_package_and_overlay_identity(self):
        item = audit_item("owner/repo/a", "python")
        artifact = artifact_input("python", [item])
        package = self.label_package(artifact, {item.key: ("consistent", None)})
        package["labels"][0]["human_verdict"] = "inconsistent"
        with self.assertRaisesRegex(ValueError, "package identity"):
            core.build_overlay(artifact, package)
        with tempfile.TemporaryDirectory() as directory:
            package, coordinator, rubric = self.bound_package(
                artifact, {item.key: ("consistent", None)}, Path(directory))
            overlay = core.build_overlay(artifact, package)
            overlay["generation_mode"] = "sample"
            with self.assertRaisesRegex(ValueError, "derivation"):
                core.rescore_overlay(artifact, overlay, label_package=package,
                                     coordinator=coordinator, rubric=rubric)

    def test_repository_split_has_no_overlap_and_is_stable(self):
        labels = []
        coordinator = []
        for language in core.LANGUAGES:
            for repo in range(6):
                for verdict in ("consistent", "inconsistent"):
                    blind = f"{language}-{repo}-{verdict}"
                    labels.append(core.CombinedLabel(blind, verdict,
                                                     "direct-mismatch" if verdict == "inconsistent" else None,
                                                     False, None, ()))
                    coordinator.append({"blind_id": blind, "language": language,
                                        "id": f"owner/repo-{language}-{repo}/f"})
        combined = core.CombinedLabels("audit", "f" * 64, tuple(labels))
        split = core.split_by_repository(combined, coordinator, split_key=b"s" * 32)
        self.assertFalse(set(split.development_repositories) & set(split.holdout_repositories))
        again = core.split_by_repository(combined, list(reversed(coordinator)), split_key=b"s" * 32)
        self.assertEqual(split.development_ids, again.development_ids)
        label_by_id = {label.blind_id: label for label in combined.labels}
        for ids in (split.development_ids, split.holdout_ids):
            cells = {(next(row["language"] for row in coordinator if row["blind_id"] == identifier),
                      label_by_id[identifier].final_verdict,
                      label_by_id[identifier].final_category) for identifier in ids}
            self.assertEqual(len(cells), len(core.LANGUAGES) * 2)

    def test_repository_split_allocates_rare_cells_or_fails_closed(self):
        labels, coordinator = [], []
        rows = [(f"c{index}", "consistent") for index in range(5)] + [
            (f"i{index}", "inconsistent") for index in range(5)]
        for repo, verdict in rows:
            blind = f"blind-{repo}"
            labels.append(core.CombinedLabel(
                blind, verdict, "direct-mismatch" if verdict == "inconsistent" else None,
                False, None, ()))
            coordinator.append({"blind_id": blind, "language": "python",
                                "id": f"owner/{repo}/file"})
        combined = core.CombinedLabels("audit", "f" * 64, tuple(labels))
        split = core.split_by_repository(combined, coordinator, split_key=b"x" * 32)
        label_by_id = {label.blind_id: label for label in labels}
        for identifiers in (split.development_ids, split.holdout_ids):
            self.assertEqual({label_by_id[item].final_verdict for item in identifiers},
                             {"consistent", "inconsistent"})
        sparse_labels = (labels[0], labels[1], labels[5])
        sparse_ids = {label.blind_id for label in sparse_labels}
        with self.assertRaisesRegex(ValueError, "twice"):
            core.split_by_repository(
                core.CombinedLabels("audit", "f" * 64, sparse_labels),
                [row for row in coordinator if row["blind_id"] in sparse_ids],
                split_key=b"x" * 32)

    def test_repository_split_rejects_gross_row_imbalance_from_skewed_repositories(self):
        labels, coordinator = [], []
        for repo, size in (("huge", 100), ("a", 1), ("b", 1), ("c", 1)):
            for index in range(size):
                blind = f"{repo}-{index}"
                labels.append(core.CombinedLabel(blind, "consistent", None, False, None, ()))
                coordinator.append({"blind_id": blind, "language": "python",
                                    "id": f"owner/{repo}/file-{index}"})
        with self.assertRaisesRegex(ValueError, "row.*tolerance"):
            core.split_by_repository(core.CombinedLabels("audit", "f" * 64, tuple(labels)),
                                     coordinator, split_key=b"z" * 32)

    def test_split_export_contains_human_labels_not_heuristic_labels(self):
        label = core.CombinedLabel("blind", "inconsistent", "over-promise", False, None, ())
        row = core.human_export_row(label, {"blind_id": "blind", "id": "o/r/f", "language": "go",
                                            "label": "consistent", "category": None})
        self.assertEqual(row["human_verdict"], "inconsistent")
        self.assertEqual(row["human_category"], "over-promise")
        self.assertNotIn("label", row)
        self.assertNotIn("category", row)


class LabelAuditIdentityTests(unittest.TestCase):
    def test_coordinator_and_packet_identities_detect_mutation(self):
        artifacts = LabelAuditSamplingTests().synthetic_artifacts()
        selection = core.build_sample(artifacts, {}, audit_id="audit")
        with tempfile.TemporaryDirectory() as directory:
            outputs = core.write_blinded_packets(selection, Path(directory) / "audit",
                                                  blind_key=b"k" * 32, rubric_sha256="f" * 64,
                                                  repo=Path.cwd())
            coordinator = core.load_coordinator(outputs.coordinator)
            packet = core.load_packet(outputs.annotator_a)
            self.assertEqual(packet["coordinator_sha256"], coordinator["coordinator_sha256"])
            changed = json.loads(outputs.coordinator.read_text())
            changed["items"][0]["inclusion_probability"] = 0.9
            outputs.coordinator.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "identity"):
                core.load_coordinator(outputs.coordinator)

    def test_private_output_refuses_repo_relative_and_existing_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory) / "report.json"
            core.write_private_json(external, {"ok": True}, repo=Path.cwd())
            self.assertEqual(stat.S_IMODE(external.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                core.write_private_json(external, {"ok": False}, repo=Path.cwd())
        with self.assertRaisesRegex(ValueError, "absolute"):
            core.write_private_json(Path("relative.json"), {}, repo=Path.cwd())
        with self.assertRaisesRegex(ValueError, "outside"):
            core.write_private_json(Path.cwd() / "report.json", {}, repo=Path.cwd())


class LabelAuditSourceTests(unittest.TestCase):
    def test_source_manifest_verifies_tracked_pool_hashes_and_counts(self):
        manifest = core.load_source_manifest(
            Path("eval/bench/human-audit/source-pools.json"), Path.cwd())
        by_language = manifest.by_language()
        self.assertEqual(by_language["python"].status, "available")
        self.assertEqual(by_language["python"].row_count, 400)
        self.assertEqual(by_language["typescript"].status, "missing")

    def test_source_manifest_rejects_consistent_non_authoritative_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            replacement = Path(directory) / "source-pools.json"
            replacement.write_bytes(Path("eval/bench/human-audit/source-pools.json").read_bytes())
            with self.assertRaisesRegex(ValueError, "authoritative"):
                core.load_source_manifest(replacement, Path.cwd())

    def test_future_derived_rows_preserve_source_provenance(self):
        from eval.bench import codocbench_to_jsonl
        source = {
            "owner": "owner", "project": "project", "file": "src/lib.rs",
            "commit": "0123456789ab", "function": "f", "language": "rust",
            "version_data": [
                {"docstring": "Old docs", "code": "fn f() {}"},
                {"docstring": "New docs", "code": "fn f() { println!() }"},
            ],
        }
        result = codocbench_to_jsonl.pair(source, 3, "old", "inconsistent")
        self.assertEqual(result["source"], {
            "owner": "owner", "project": "project", "file": "src/lib.rs",
            "commit": "0123456789ab", "doc_version": "old",
        })
        self.assertEqual(result["source_status"], "complete")

    def test_source_registry_records_missing_pools_without_fake_hashes(self):
        registry = json.loads(Path("eval/bench/human-audit/source-pools.json").read_text())
        by_language = {row["language"]: row for row in registry["pools"]}
        self.assertEqual(by_language["python"]["sha256"],
                         "6cbccfb5eb88f2a7e826e3e5f3595fb59274e04a2711c7c097d8faac4926fdae")
        for language, missing in (("typescript", 76), ("rust", 56), ("go", 61)):
            self.assertEqual(by_language[language]["status"], "missing")
            self.assertEqual(by_language[language]["missing_discarded_count"], missing)
            self.assertNotIn("sha256", by_language[language])


class LabelAuditCliTests(unittest.TestCase):
    def test_help_lists_six_provider_free_subcommands(self):
        completed = subprocess.run(
            [sys.executable, "eval/bench/label_audit.py", "--help"],
            capture_output=True, text=True, cwd=Path.cwd(),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for command in ("sample", "check-labels", "make-third-review", "report", "rescore", "split"):
            self.assertIn(command, completed.stdout)
        self.assertNotIn("--model", completed.stdout)
        self.assertNotIn("--provider", completed.stdout)
        rescore = subprocess.run(
            [sys.executable, "eval/bench/label_audit.py", "rescore", "--help"],
            capture_output=True, text=True, cwd=Path.cwd(),
        )
        self.assertEqual(rescore.returncode, 0, rescore.stderr)
        for required in ("--label-package", "--coordinator", "--rubric"):
            self.assertIn(required, rescore.stdout)
        sample = subprocess.run(
            [sys.executable, "eval/bench/label_audit.py", "sample", "--help"],
            capture_output=True, text=True, cwd=Path.cwd(),
        )
        self.assertEqual(sample.returncode, 0, sample.stderr)
        self.assertNotIn("--source-manifest", sample.stdout)

    def test_sample_stops_at_human_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = []
            for language in core.LANGUAGES:
                rows = [result_row(f"o/{language}/p", language, "inconsistent", "complete", "inconsistent"),
                        result_row(f"o/{language}/fp", language, "consistent", "complete", "inconsistent")]
                rows += [result_row(f"o/{language}/tn-{i}", language) for i in range(25)]
                if language == "rust":
                    rows.append(result_row("o/rust/a", language, status="abstain", verdict=None))
                path = root / f"{language}.json"
                path.write_text(json.dumps({"schema_version": 2,
                                            "metadata": {"dataset": {"sha256": "d" * 64}},
                                            "rows": rows, "timing": {}}))
                artifacts += ["--artifact", str(path)]
            rubric = root / "rubric.md"
            rubric.write_text("Human rubric")
            work = root / "external" / "audit"
            completed = subprocess.run(
                [sys.executable, "eval/bench/label_audit.py", "sample", *artifacts,
                 "--audit-id", "test-audit", "--rubric", str(rubric),
                 "--work-dir", str(work)], cwd=Path.cwd(), capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("HUMAN JUDGMENT REQUIRED", completed.stdout)
            self.assertFalse(list(work.glob("*annotation*")))
            self.assertFalse(list(work.glob("*adjudication*")))


if __name__ == "__main__":
    unittest.main()
