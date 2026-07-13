import hashlib
import json
import os
import stat
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

    def test_load_annotations_requires_exact_packet_and_self_attestation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packet = root / "packet.json"
            labels = root / "labels.json"
            packet.write_text(json.dumps({"schema_version": 1, "audit_id": "audit",
                                          "rubric_sha256": "f" * 64,
                                          "items": [{"blind_id": "item-a"}]}))
            labels.write_text(json.dumps({
                "schema_version": 1, "audit_id": "audit", "rubric_sha256": "f" * 64,
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


class LabelAuditOverlayTests(unittest.TestCase):
    def test_rescore_requires_complete_content_bound_overlay(self):
        item_a = audit_item("owner/repo/a", "python", "inconsistent", "consistent")
        item_b = audit_item("owner/repo/b", "python", "consistent", "consistent")
        artifact = artifact_input("python", [item_a, item_b])
        partial = core.build_overlay(artifact, {
            item_a.key: ("inconsistent", "direct-mismatch"),
        }, rubric_sha256="f" * 64, label_package_sha256="e" * 64)
        with self.assertRaisesRegex(ValueError, "100%"):
            core.rescore_overlay(artifact, partial)
        complete = core.build_overlay(artifact, {
            item_a.key: ("consistent", None), item_b.key: ("consistent", None),
        }, rubric_sha256="f" * 64, label_package_sha256="e" * 64)
        result = core.rescore_overlay(artifact, complete)
        self.assertEqual(result["corrected"]["tn"], 2)
        self.assertEqual(result["corrected"]["tp"], 0)

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


if __name__ == "__main__":
    unittest.main()
