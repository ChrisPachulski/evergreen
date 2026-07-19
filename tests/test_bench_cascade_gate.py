import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from eval.bench import cascade_gate


EMPTY_SHA = hashlib.sha256(b"").hexdigest()


# -- fixture builders -----------------------------------------------------------------------

def clear_execution(attempts=1):
    return {
        "strategy": "cascade-v1", "route": "clear", "logical_calls": 1,
        "provider_attempts": attempts, "attempts_by_tier": {"cheap": attempts, "strong": 0},
        "attempts_by_stage": {"screen": attempts},
    }


def jury_execution(screen=1, snap=1, challenge=1, prongs=3, blindspot=1):
    total = screen + snap + challenge + prongs + blindspot
    return {
        "strategy": "cascade-v1", "route": "jury", "logical_calls": 5,
        "provider_attempts": total,
        "attempts_by_tier": {"cheap": screen + challenge + prongs, "strong": snap + blindspot},
        "attempts_by_stage": {
            "screen": screen, "snap": snap, "challenge": challenge, "prongs": prongs,
            "blindspot": blindspot,
        },
    }


def dataset_row(pair_id, label, language="python", category=None):
    return {
        "id": pair_id, "label": label, "language": language, "category": category,
        "code": "return 1", "doc": "returns one", "func": "f", "source": "unit-test",
        "source_status": "validated",
    }


def jsonl_bytes(rows):
    return b"".join(json.dumps(row, sort_keys=True).encode() + b"\n" for row in rows)


def clear_row(pair_id, label, language="python", category=None, attempts=1):
    return {
        "id": pair_id, "label": label, "language": language, "category": category,
        "got": {
            "final_status": "complete", "semantic_status": "decided",
            "final_verdict": "consistent", "category": None,
            "execution": clear_execution(attempts),
        },
    }


def jury_row(pair_id, label, predicted, language="python", category=None, **attempts):
    return {
        "id": pair_id, "label": label, "language": language, "category": category,
        "got": {
            "final_status": "complete", "semantic_status": "decided",
            "final_verdict": predicted, "category": None,
            "execution": jury_execution(**attempts),
        },
    }


def abstain_row(pair_id, label, language="python", category=None):
    return {
        "id": pair_id, "label": label, "language": language, "category": category,
        "got": {
            "final_status": "abstain", "semantic_status": "not-evaluated", "final_verdict": None,
        },
    }


def v3_metadata(dataset_sha256, max_provider_attempts=1000, resolver="v3",
                 dataset_path="eval/bench/probe.jsonl"):
    return {
        "dataset": {"path": dataset_path, "sha256": dataset_sha256},
        "provider": "claude",
        "skill": {"path": "skills/evergreen/SKILL.md", "sha256": "2" * 64},
        "judge": {
            "path": "eval/bench/run_bench.py", "sha256": "3" * 64,
            "files": [{"path": "eval/bench/run_bench.py", "sha256": "4" * 64}],
        },
        "git": {
            "commit": "5" * 40, "tree": "6" * 40, "dirty": False,
            "status_sha256": EMPTY_SHA, "diff_sha256": EMPTY_SHA, "untracked_sha256": EMPTY_SHA,
        },
        "cli_version": "2.7.1 (Claude Code)",
        "settings": {
            "provider": "claude", "resolver": resolver,
            "models": {"strong": "opus", "cheap": "sonnet"}, "concurrency": 4,
            "max_provider_attempts": max_provider_attempts,
        },
    }


def receipt_document(rows_by_id_label, dataset_sha256):
    positive = sum(1 for label in rows_by_id_label.values() if label == "inconsistent")
    control = sum(1 for label in rows_by_id_label.values() if label == "consistent")
    return {
        "schema_version": 1, "selection_protocol": "test", "language": "python",
        "positive_label": "inconsistent", "control_label": "consistent",
        "positive_count": positive, "control_count": control,
        "parent_dataset_sha256": "a" * 64, "output_dataset_sha256": dataset_sha256,
        "rows": [{"id": pair_id, "label": label} for pair_id, label in rows_by_id_label.items()],
    }


class ProbeFixture:
    """A bound 50/50 probe (dataset + receipt + v3 artifact) with an exact confusion matrix.

    tp positives correctly flagged by the jury; fn_clear positives auto-cleared by the cheap
    screen (the systematic-flaw signal); fn_jury positives reach the jury but are still missed;
    fp negatives incorrectly flagged by the jury; tn negatives correctly auto-cleared.
    """

    def __init__(self, tp=40, fn_clear=5, fn_jury=5, fp=10, tn=40, max_provider_attempts=1000):
        assert tp + fn_clear + fn_jury == cascade_gate.PROBE_POSITIVE_COUNT
        assert fp + tn == cascade_gate.PROBE_CONTROL_COUNT
        self.dataset_rows = {}
        self.artifact_rows = []
        self.fn_clear_ids = []

        def add(pair_id, label, row):
            self.dataset_rows[pair_id] = label
            self.artifact_rows.append(row)

        for i in range(tp):
            pid = f"org/repo/tp{i}#pos"
            add(pid, "inconsistent", jury_row(pid, "inconsistent", "inconsistent"))
        for i in range(fn_clear):
            pid = f"org/repo/fnclear{i}#pos"
            add(pid, "inconsistent", clear_row(pid, "inconsistent"))
            self.fn_clear_ids.append(pid)
        for i in range(fn_jury):
            pid = f"org/repo/fnjury{i}#pos"
            add(pid, "inconsistent", jury_row(pid, "inconsistent", "consistent"))
        for i in range(fp):
            pid = f"org/repo/fp{i}#ctl"
            add(pid, "consistent", jury_row(pid, "consistent", "inconsistent"))
        for i in range(tn):
            pid = f"org/repo/tn{i}#ctl"
            add(pid, "consistent", clear_row(pid, "consistent"))

        self.dataset_payload = jsonl_bytes(
            dataset_row(pair_id, label) for pair_id, label in self.dataset_rows.items()
        )
        self.dataset_sha256 = hashlib.sha256(self.dataset_payload).hexdigest()
        self.receipt = receipt_document(self.dataset_rows, self.dataset_sha256)
        self.metadata = v3_metadata(
            self.dataset_sha256, max_provider_attempts=max_provider_attempts
        )
        self.document = {
            "schema_version": 1, "metadata": self.metadata,
            "timing": {"started_at": "2026-07-19T00:00:00Z", "elapsed_seconds": 1.0},
            "rows": self.artifact_rows,
        }

    def write(self, root):
        root = Path(root)
        dataset_path = root / "probe.jsonl"
        receipt_path = root / "probe.receipt.json"
        artifact_path = root / "artifact.json"
        dataset_path.write_bytes(self.dataset_payload)
        receipt_path.write_text(json.dumps(self.receipt, indent=1, sort_keys=True) + "\n")
        artifact_path.write_text(json.dumps(self.document, indent=2, sort_keys=True) + "\n")
        return dataset_path, receipt_path, artifact_path

    def identity_overlay(self):
        """An adjudicated overlay that confirms every nominal label unchanged (a clean probe)."""
        return {
            "schema_version": 1,
            "rows": [{"id": pid, "label": label} for pid, label in self.dataset_rows.items()],
        }


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def write_json(self, name, document):
        path = self.root / name
        path.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")
        return path


# -- probe binding: receipt + dataset ---------------------------------------------------------

class ProbeBindingTests(TempDirTestCase):
    def test_binds_a_matching_dataset_and_receipt(self):
        fixture = ProbeFixture()
        dataset_path, receipt_path, _artifact_path = fixture.write(self.root)
        receipt, receipt_sha256 = cascade_gate.load_receipt(receipt_path)
        dataset_sha256, rows_by_id = cascade_gate.bind_probe_dataset(dataset_path, receipt)
        self.assertEqual(dataset_sha256, fixture.dataset_sha256)
        self.assertEqual(len(rows_by_id), 100)
        self.assertTrue(receipt_sha256)

    def test_rejects_a_receipt_that_is_not_a_50_50_probe(self):
        fixture = ProbeFixture()
        _dataset_path, receipt_path, _artifact_path = fixture.write(self.root)
        receipt = json.loads(receipt_path.read_text())
        receipt["positive_count"] = 49
        del receipt["rows"][0]
        bad_path = self.write_json("bad.receipt.json", receipt)
        with self.assertRaisesRegex(ValueError, "50/50"):
            cascade_gate.load_receipt(bad_path)

    def test_rejects_a_dataset_that_no_longer_matches_the_receipts_hash(self):
        fixture = ProbeFixture()
        dataset_path, receipt_path, _artifact_path = fixture.write(self.root)
        receipt, _sha = cascade_gate.load_receipt(receipt_path)
        dataset_path.write_bytes(fixture.dataset_payload + b'{"id":"extra","label":"consistent"}\n')
        with self.assertRaisesRegex(ValueError, "sha256"):
            cascade_gate.bind_probe_dataset(dataset_path, receipt)

    def test_rejects_a_dataset_whose_ids_do_not_match_the_receipt(self):
        fixture = ProbeFixture()
        receipt = dict(fixture.receipt)
        receipt["rows"] = list(receipt["rows"])
        receipt["rows"][0] = {"id": "some/other#id", "label": receipt["rows"][0]["label"]}
        # The receipt now claims a different id set than the dataset bytes actually contain;
        # rehash so the sha256 gate itself still matches (isolating the id-mismatch check).
        with tempfile.TemporaryDirectory() as other:
            other_dataset = Path(other) / "probe.jsonl"
            other_dataset.write_bytes(fixture.dataset_payload)
            with self.assertRaisesRegex(ValueError, "ids do not match"):
                cascade_gate.bind_probe_dataset(other_dataset, receipt)


# -- v3 artifact binding -----------------------------------------------------------------------

class ArtifactBindingTests(TempDirTestCase):
    def test_binds_a_complete_matching_artifact(self):
        fixture = ProbeFixture()
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)
        receipt, _sha = cascade_gate.load_receipt(receipt_path)
        dataset_sha256, rows_by_id = cascade_gate.bind_probe_dataset(dataset_path, receipt)
        _artifact_sha256, metadata, rows = cascade_gate.load_probe_artifact(artifact_path)
        cascade_gate.bind_artifact_to_probe(metadata, rows, dataset_sha256, rows_by_id)  # no raise

    def test_rejects_an_artifact_declaring_a_non_v3_resolver(self):
        fixture = ProbeFixture()
        fixture.metadata["settings"]["resolver"] = "v2"
        fixture.document["metadata"] = fixture.metadata
        _dataset_path, _receipt_path, artifact_path = fixture.write(self.root)
        with self.assertRaisesRegex(ValueError, "resolver v3"):
            cascade_gate.load_probe_artifact(artifact_path)

    def test_rejects_an_artifact_whose_dataset_sha256_does_not_match_the_probe(self):
        fixture = ProbeFixture()
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)
        receipt, _sha = cascade_gate.load_receipt(receipt_path)
        dataset_sha256, rows_by_id = cascade_gate.bind_probe_dataset(dataset_path, receipt)
        _artifact_sha256, metadata, rows = cascade_gate.load_probe_artifact(artifact_path)
        tampered_metadata = {**metadata, "dataset": {**metadata["dataset"], "sha256": "f" * 64}}
        with self.assertRaisesRegex(ValueError, "dataset sha256"):
            cascade_gate.bind_artifact_to_probe(tampered_metadata, rows, dataset_sha256, rows_by_id)

    def test_rejects_an_artifact_missing_a_probe_row(self):
        fixture = ProbeFixture()
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)
        receipt, _sha = cascade_gate.load_receipt(receipt_path)
        dataset_sha256, rows_by_id = cascade_gate.bind_probe_dataset(dataset_path, receipt)
        _artifact_sha256, metadata, rows = cascade_gate.load_probe_artifact(artifact_path)
        with self.assertRaisesRegex(ValueError, "missing 1"):
            cascade_gate.bind_artifact_to_probe(metadata, rows[1:], dataset_sha256, rows_by_id)

    def test_rejects_an_artifact_with_an_unexpected_extra_row(self):
        fixture = ProbeFixture()
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)
        receipt, _sha = cascade_gate.load_receipt(receipt_path)
        dataset_sha256, rows_by_id = cascade_gate.bind_probe_dataset(dataset_path, receipt)
        _artifact_sha256, metadata, rows = cascade_gate.load_probe_artifact(artifact_path)
        extra = clear_row("org/repo/unexpected#ctl", "consistent")
        with self.assertRaisesRegex(ValueError, "unexpected 1"):
            cascade_gate.bind_artifact_to_probe(metadata, rows + [extra], dataset_sha256, rows_by_id)

    def test_rejects_an_abstained_row_as_incomplete(self):
        fixture = ProbeFixture()
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)
        receipt, _sha = cascade_gate.load_receipt(receipt_path)
        dataset_sha256, rows_by_id = cascade_gate.bind_probe_dataset(dataset_path, receipt)
        _artifact_sha256, metadata, rows = cascade_gate.load_probe_artifact(artifact_path)
        rows = list(rows)
        replaced_id = rows[0]["id"]
        rows[0] = abstain_row(replaced_id, rows[0]["label"])
        with self.assertRaisesRegex(ValueError, "abstention"):
            cascade_gate.bind_artifact_to_probe(metadata, rows, dataset_sha256, rows_by_id)


# -- adjudicated label overlay -------------------------------------------------------------------

class AdjudicationOverlayTests(TempDirTestCase):
    def test_accepts_an_overlay_covering_exactly_the_probe_ids(self):
        fixture = ProbeFixture()
        overlay_path = self.write_json("adjudicated.json", fixture.identity_overlay())
        overlay = cascade_gate.load_adjudicated_overlay(overlay_path, set(fixture.dataset_rows))
        self.assertEqual(len(overlay), 100)

    def test_rejects_an_overlay_missing_a_probe_id(self):
        fixture = ProbeFixture()
        document = fixture.identity_overlay()
        document["rows"] = document["rows"][:-1]
        overlay_path = self.write_json("adjudicated.json", document)
        with self.assertRaisesRegex(ValueError, "exactly the probe"):
            cascade_gate.load_adjudicated_overlay(overlay_path, set(fixture.dataset_rows))

    def test_rejects_an_overlay_with_an_id_outside_the_probe(self):
        fixture = ProbeFixture()
        document = fixture.identity_overlay()
        document["rows"][0] = {"id": "org/repo/outsider#ctl", "label": "consistent"}
        overlay_path = self.write_json("adjudicated.json", document)
        with self.assertRaisesRegex(ValueError, "exactly the probe"):
            cascade_gate.load_adjudicated_overlay(overlay_path, set(fixture.dataset_rows))


# -- quality gate boundaries ----------------------------------------------------------------------

class QualityGateBoundaryTests(unittest.TestCase):
    thresholds = (0.80, 0.80, 0.80)

    def test_precision_exactly_at_threshold_passes(self):
        gate = cascade_gate.evaluate_quality_gate(
            None, {"precision": 0.80, "recall": 0.90, "f1": 0.90}, self.thresholds
        )
        self.assertTrue(gate["passed"])

    def test_precision_just_below_threshold_fails(self):
        gate = cascade_gate.evaluate_quality_gate(
            None, {"precision": 0.7999999, "recall": 0.90, "f1": 0.90}, self.thresholds
        )
        self.assertFalse(gate["passed"])

    def test_recall_exactly_at_threshold_passes(self):
        gate = cascade_gate.evaluate_quality_gate(
            None, {"precision": 0.90, "recall": 0.80, "f1": 0.90}, self.thresholds
        )
        self.assertTrue(gate["passed"])

    def test_recall_just_below_threshold_fails(self):
        gate = cascade_gate.evaluate_quality_gate(
            None, {"precision": 0.90, "recall": 0.7999999, "f1": 0.90}, self.thresholds
        )
        self.assertFalse(gate["passed"])

    def test_f1_exactly_at_threshold_passes(self):
        gate = cascade_gate.evaluate_quality_gate(
            None, {"precision": 0.90, "recall": 0.90, "f1": 0.80}, self.thresholds
        )
        self.assertTrue(gate["passed"])

    def test_f1_just_below_threshold_fails(self):
        gate = cascade_gate.evaluate_quality_gate(
            None, {"precision": 0.90, "recall": 0.90, "f1": 0.7999999}, self.thresholds
        )
        self.assertFalse(gate["passed"])

    def test_missing_adjudicated_metrics_fails_even_when_nominal_would_pass(self):
        gate = cascade_gate.evaluate_quality_gate(
            {"precision": 1.0, "recall": 1.0, "f1": 1.0}, None, self.thresholds
        )
        self.assertFalse(gate["passed"])
        self.assertIn("adjudicated", gate["reason"])

    def test_unavailable_adjudicated_metrics_fails(self):
        gate = cascade_gate.evaluate_quality_gate(
            None, {"precision": None, "recall": None, "f1": None}, self.thresholds
        )
        self.assertFalse(gate["passed"])


class QualityGateIntegrationTests(TempDirTestCase):
    def test_exact_boundary_probe_passes_when_adjudication_confirms_nominal(self):
        fixture = ProbeFixture(tp=40, fn_clear=5, fn_jury=5, fp=10, tn=40)
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)
        overlay_path = self.write_json("adjudicated.json", fixture.identity_overlay())

        packet = cascade_gate.build_decision_packet(
            artifact_path, dataset_path, receipt_path, adjudicated_path=overlay_path,
            accept_projected_cost_gate=True,
        )

        self.assertEqual(packet["quality"]["nominal"]["precision"], 0.8)
        self.assertEqual(packet["quality"]["nominal"]["recall"], 0.8)
        self.assertAlmostEqual(packet["quality"]["nominal"]["f1"], 0.8)
        self.assertTrue(packet["gates"]["quality"]["passed"])

    def test_probe_just_below_precision_threshold_fails(self):
        fixture = ProbeFixture(tp=40, fn_clear=5, fn_jury=5, fp=11, tn=39)
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)
        overlay_path = self.write_json("adjudicated.json", fixture.identity_overlay())

        packet = cascade_gate.build_decision_packet(
            artifact_path, dataset_path, receipt_path, adjudicated_path=overlay_path,
        )

        self.assertLess(packet["quality"]["adjudicated"]["precision"], 0.80)
        self.assertFalse(packet["gates"]["quality"]["passed"])
        self.assertFalse(packet["passed"])

    def test_missing_adjudication_fails_the_whole_packet_even_with_perfect_nominal_metrics(self):
        fixture = ProbeFixture(tp=50, fn_clear=0, fn_jury=0, fp=0, tn=50)
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)

        packet = cascade_gate.build_decision_packet(artifact_path, dataset_path, receipt_path)

        self.assertEqual(packet["quality"]["nominal"]["precision"], 1.0)
        self.assertIsNone(packet["quality"]["adjudicated"])
        self.assertFalse(packet["gates"]["quality"]["passed"])
        self.assertFalse(packet["passed"])


# -- auto-clear false negatives -----------------------------------------------------------------

class AutoClearFalseNegativeTests(TempDirTestCase):
    def test_surfaces_exactly_the_auto_cleared_missed_drift_rows(self):
        fixture = ProbeFixture(tp=40, fn_clear=5, fn_jury=5, fp=10, tn=40)
        found = cascade_gate.auto_clear_false_negatives(fixture.artifact_rows)
        self.assertEqual(found, sorted(fixture.fn_clear_ids))
        self.assertEqual(len(found), 5)

    def test_jury_routed_false_negatives_are_not_counted_as_auto_clear(self):
        fixture = ProbeFixture(tp=40, fn_clear=0, fn_jury=10, fp=10, tn=40)
        found = cascade_gate.auto_clear_false_negatives(fixture.artifact_rows)
        self.assertEqual(found, [])

    def test_adjudication_can_reveal_an_auto_clear_false_negative_nominal_missed(self):
        fixture = ProbeFixture(tp=40, fn_clear=5, fn_jury=5, fp=10, tn=40)
        flipped_tn_id = next(
            row["id"] for row in fixture.artifact_rows
            if row["id"].startswith("org/repo/tn")
        )
        overlay = {flipped_tn_id: "inconsistent"}
        for row in fixture.artifact_rows:
            overlay.setdefault(row["id"], row["label"])

        nominal = cascade_gate.auto_clear_false_negatives(fixture.artifact_rows)
        adjudicated = cascade_gate.auto_clear_false_negatives(fixture.artifact_rows, overlay)

        self.assertEqual(len(nominal), 5)
        self.assertEqual(len(adjudicated), 6)
        self.assertIn(flipped_tn_id, adjudicated)


# -- cost gate boundaries -------------------------------------------------------------------------

class CostGateMeasuredBoundaryTests(unittest.TestCase):
    def test_reduction_exactly_at_threshold_passes(self):
        actual = {"provider_attempts": 30}
        counterfactual = {"basis": "measured", "provider_attempts": 100}
        gate = cascade_gate.evaluate_cost_gate(actual, counterfactual, 0.70, False)
        self.assertEqual(gate["reduction"], 0.70)
        self.assertTrue(gate["passed"])
        self.assertFalse(gate["requires_human_acceptance"])

    def test_reduction_just_below_threshold_fails(self):
        actual = {"provider_attempts": 31}
        counterfactual = {"basis": "measured", "provider_attempts": 100}
        gate = cascade_gate.evaluate_cost_gate(actual, counterfactual, 0.70, False)
        self.assertLess(gate["reduction"], 0.70)
        self.assertFalse(gate["passed"])


class CostGateProjectedBoundaryTests(unittest.TestCase):
    def counterfactual(self, floor):
        return {
            "basis": "projected", "logical_calls_floor": floor,
            "logical_calls_ceiling": floor * 2, "logical_calls_expected": None,
        }

    def test_projected_pass_requires_explicit_acceptance_even_at_threshold(self):
        actual = {"provider_attempts": 18}  # 1 - 18/60 == 0.70 exactly
        gate = cascade_gate.evaluate_cost_gate(actual, self.counterfactual(60), 0.70, False)
        self.assertTrue(gate["meets_threshold_conservative"])
        self.assertFalse(gate["passed"])
        self.assertTrue(gate["requires_human_acceptance"])

    def test_projected_passes_at_threshold_once_accepted(self):
        actual = {"provider_attempts": 18}
        gate = cascade_gate.evaluate_cost_gate(actual, self.counterfactual(60), 0.70, True)
        self.assertTrue(gate["passed"])

    def test_projected_below_threshold_still_fails_when_accepted(self):
        actual = {"provider_attempts": 19}  # 1 - 19/60 < 0.70
        gate = cascade_gate.evaluate_cost_gate(actual, self.counterfactual(60), 0.70, True)
        self.assertFalse(gate["meets_threshold_conservative"])
        self.assertFalse(gate["passed"])


# -- full-v2 counterfactual: measured vs projected -------------------------------------------------

class CounterfactualTests(unittest.TestCase):
    def test_measured_counterfactual_sums_a_ledger_bearing_full_v2_artifact(self):
        rows = [
            jury_row("a", "inconsistent", "inconsistent"),
            jury_row("b", "consistent", "consistent"),
        ]
        result = cascade_gate.measured_full_v2_counterfactual(rows, {"a", "b"})
        self.assertEqual(result["basis"], "measured")
        self.assertEqual(result["provider_attempts"], 14)

    def test_measured_counterfactual_is_unavailable_without_a_ledger(self):
        rows = [
            {"id": "a", "label": "inconsistent", "language": "python", "category": None,
             "got": {"final_status": "complete", "final_verdict": "inconsistent"}},
        ]
        result = cascade_gate.measured_full_v2_counterfactual(rows, {"a"})
        self.assertIsNone(result)

    def test_measured_counterfactual_rejects_a_row_set_not_matching_the_probe(self):
        rows = [jury_row("a", "inconsistent", "inconsistent")]
        with self.assertRaisesRegex(ValueError, "exactly the probe"):
            cascade_gate.measured_full_v2_counterfactual(rows, {"a", "b"})

    def test_projected_reconstruction_uses_a_six_call_floor_per_row(self):
        result = cascade_gate.projected_full_v2_counterfactual(100)
        self.assertEqual(result["logical_calls_floor"], 600)
        self.assertEqual(result["logical_calls_ceiling"], 1000)
        self.assertIsNone(result["logical_calls_expected"])
        self.assertIsNone(result["provider_attempts"])

    def test_projected_reconstruction_folds_in_historical_escalation_and_synthesis_rates(self):
        lane = {"schema_version": 1, "lane_id": "python-v2-dev", "escalated_prong_rate": 0.20,
                 "synthesis_rate": 0.50}
        result = cascade_gate.projected_full_v2_counterfactual(100, lane)
        # 100 * (6 + 3*0.20 + 1*0.50) = 100 * 7.1 = 710
        self.assertAlmostEqual(result["logical_calls_expected"], 710)
        self.assertEqual(result["historical_lane_id"], "python-v2-dev")


class HistoricalLaneSummaryTests(TempDirTestCase):
    def test_loads_a_valid_summary(self):
        path = self.write_json("lane.json", {
            "schema_version": 1, "lane_id": "python-v2-dev",
            "escalated_prong_rate": 0.2, "synthesis_rate": 0.3,
        })
        lane = cascade_gate.load_historical_lane_summary(path)
        self.assertEqual(lane["escalated_prong_rate"], 0.2)

    def test_rejects_a_rate_outside_zero_to_one(self):
        path = self.write_json("lane.json", {
            "schema_version": 1, "escalated_prong_rate": 1.5, "synthesis_rate": 0.3,
        })
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            cascade_gate.load_historical_lane_summary(path)


# -- end-to-end decision packet: cost side -------------------------------------------------------

class CostGateIntegrationTests(TempDirTestCase):
    def test_projected_cost_gate_without_acceptance_fails_even_past_threshold(self):
        # 45 clear rows @1 attempt + 55 jury rows @7 attempts = 430 actual provider attempts,
        # against a 100-row floor of 600 -> reduction_conservative = 1 - 430/600 = 0.2833... < 0.70,
        # so this also documents that a wide-margin cascade can still fail the conservative floor;
        # exercise the "no acceptance" branch specifically here.
        fixture = ProbeFixture(tp=40, fn_clear=5, fn_jury=5, fp=10, tn=40)
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)

        packet = cascade_gate.build_decision_packet(artifact_path, dataset_path, receipt_path)

        self.assertEqual(packet["gates"]["cost"]["basis"], "projected")
        self.assertFalse(packet["gates"]["cost"]["passed"])
        self.assertTrue(packet["gates"]["cost"]["requires_human_acceptance"])

    def test_cost_gate_prefers_a_measured_counterfactual_when_supplied(self):
        # Every row auto-clears — best case for cost, worst case for quality; this test isolates
        # the cost side only. v3 actual = 100 clear rows * 1 attempt = 100.
        fixture = ProbeFixture(tp=0, fn_clear=50, fn_jury=0, fp=0, tn=50)
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)

        full_v2_rows = [
            jury_row(pair_id, label, label, screen=0, snap=1, challenge=1, prongs=3, blindspot=1)
            for pair_id, label in fixture.dataset_rows.items()
        ]
        full_v2_metadata = v3_metadata(fixture.dataset_sha256, resolver="v2")
        full_v2_document = {
            "schema_version": 1, "metadata": full_v2_metadata,
            "timing": {"started_at": "2026-07-19T00:00:00Z", "elapsed_seconds": 1.0},
            "rows": full_v2_rows,
        }
        full_v2_path = self.root / "full_v2.json"
        full_v2_path.write_text(json.dumps(full_v2_document, indent=2, sort_keys=True) + "\n")

        packet = cascade_gate.build_decision_packet(
            artifact_path, dataset_path, receipt_path, full_v2_artifact_path=full_v2_path,
        )

        self.assertEqual(packet["gates"]["cost"]["basis"], "measured")
        # full-v2: 100 rows * 6 attempts = 600; v3 actual: 100 clear rows @1 = 100.
        self.assertAlmostEqual(packet["gates"]["cost"]["reduction"], 1 - 100 / 600)
        self.assertTrue(packet["gates"]["cost"]["passed"])

    def test_measured_counterfactual_missing_a_ledger_falls_back_to_projected(self):
        fixture = ProbeFixture(tp=50, fn_clear=0, fn_jury=0, fp=0, tn=50)
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)

        full_v2_rows = [
            {"id": pair_id, "label": label, "language": "python", "category": None,
             "got": {"final_status": "complete", "final_verdict":
                      "inconsistent" if label == "inconsistent" else "consistent"}}
            for pair_id, label in fixture.dataset_rows.items()
        ]
        full_v2_metadata = v3_metadata(fixture.dataset_sha256, resolver="v2")
        full_v2_document = {
            "schema_version": 1, "metadata": full_v2_metadata,
            "timing": {"started_at": "2026-07-19T00:00:00Z", "elapsed_seconds": 1.0},
            "rows": full_v2_rows,
        }
        full_v2_path = self.root / "full_v2.json"
        full_v2_path.write_text(json.dumps(full_v2_document, indent=2, sort_keys=True) + "\n")

        packet = cascade_gate.build_decision_packet(
            artifact_path, dataset_path, receipt_path, full_v2_artifact_path=full_v2_path,
        )

        self.assertEqual(packet["gates"]["cost"]["basis"], "projected")


# -- budget headroom and execution accounting in the packet -----------------------------------

class ExecutionAccountingPacketTests(TempDirTestCase):
    def test_reports_budget_headroom_from_settings(self):
        fixture = ProbeFixture(
            tp=40, fn_clear=5, fn_jury=5, fp=10, tn=40, max_provider_attempts=1000,
        )
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)

        packet = cascade_gate.build_decision_packet(artifact_path, dataset_path, receipt_path)

        execution = packet["execution"]
        self.assertIsInstance(execution["provider_attempts"], int)
        self.assertGreater(execution["provider_attempts"], 0)
        self.assertEqual(execution["budget"], 1000)
        self.assertEqual(execution["budget_headroom"], 1000 - execution["provider_attempts"])

    def test_missing_execution_ledger_fails_closed(self):
        fixture = ProbeFixture(tp=50, fn_clear=0, fn_jury=0, fp=0, tn=50)
        for row in fixture.artifact_rows:
            del row["got"]["execution"]
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)

        with self.assertRaisesRegex(ValueError, "execution ledger"):
            cascade_gate.build_decision_packet(artifact_path, dataset_path, receipt_path)


# -- CLI ---------------------------------------------------------------------------------------

class CLITests(TempDirTestCase):
    def test_cli_pass_exits_zero_and_writes_a_deterministic_json_packet(self):
        # Perfect quality (tp=50, tn=50) plus a measured full-v2 counterfactual heavy enough
        # (20 attempts/row) that v3's actual 400 attempts clear the 70% reduction outright.
        fixture = ProbeFixture(tp=50, fn_clear=0, fn_jury=0, fp=0, tn=50)
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)
        overlay_path = self.write_json("adjudicated.json", fixture.identity_overlay())
        full_v2_rows = [
            jury_row(pair_id, label, label, screen=0, snap=4, challenge=4, prongs=8, blindspot=4)
            for pair_id, label in fixture.dataset_rows.items()
        ]
        full_v2_document = {
            "schema_version": 1, "metadata": v3_metadata(fixture.dataset_sha256, resolver="v2"),
            "timing": {"started_at": "2026-07-19T00:00:00Z", "elapsed_seconds": 1.0},
            "rows": full_v2_rows,
        }
        full_v2_path = self.root / "full_v2.json"
        full_v2_path.write_text(json.dumps(full_v2_document, indent=2, sort_keys=True) + "\n")
        json_out = self.root / "packet.json"

        exit_code = cascade_gate.main([
            "--artifact", str(artifact_path), "--dataset", str(dataset_path),
            "--receipt", str(receipt_path), "--adjudicated", str(overlay_path),
            "--full-v2-artifact", str(full_v2_path), "--json", str(json_out),
        ])

        self.assertEqual(exit_code, 0)
        first = json_out.read_text()
        packet = json.loads(first)
        self.assertTrue(packet["passed"])
        self.assertEqual(packet["gates"]["cost"]["basis"], "measured")

        json_out2 = self.root / "packet2.json"
        cascade_gate.main([
            "--artifact", str(artifact_path), "--dataset", str(dataset_path),
            "--receipt", str(receipt_path), "--adjudicated", str(overlay_path),
            "--full-v2-artifact", str(full_v2_path), "--json", str(json_out2),
        ])
        self.assertEqual(first, json_out2.read_text())

    def test_cli_fail_exits_nonzero(self):
        fixture = ProbeFixture(tp=50, fn_clear=0, fn_jury=0, fp=0, tn=50)
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)
        json_out = self.root / "packet.json"

        exit_code = cascade_gate.main([
            "--artifact", str(artifact_path), "--dataset", str(dataset_path),
            "--receipt", str(receipt_path), "--json", str(json_out),
        ])

        self.assertNotEqual(exit_code, 0)
        packet = json.loads(json_out.read_text())
        self.assertFalse(packet["passed"])

    def test_cli_structural_error_fails_closed_instead_of_raising(self):
        fixture = ProbeFixture()
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)
        dataset_path.write_bytes(fixture.dataset_payload + b'{"id":"x","label":"consistent"}\n')
        json_out = self.root / "packet.json"

        exit_code = cascade_gate.main([
            "--artifact", str(artifact_path), "--dataset", str(dataset_path),
            "--receipt", str(receipt_path), "--json", str(json_out),
        ])

        self.assertNotEqual(exit_code, 0)
        packet = json.loads(json_out.read_text())
        self.assertFalse(packet["passed"])
        self.assertIn("sha256", packet["error"])

    def test_cli_writes_a_human_readable_table(self):
        fixture = ProbeFixture(tp=40, fn_clear=5, fn_jury=5, fp=10, tn=40)
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)
        json_out = self.root / "packet.json"
        table_out = self.root / "table.txt"

        cascade_gate.main([
            "--artifact", str(artifact_path), "--dataset", str(dataset_path),
            "--receipt", str(receipt_path), "--json", str(json_out), "--table", str(table_out),
        ])

        table = table_out.read_text()
        self.assertIn("cascade gate:", table)
        self.assertIn("quality gate:", table)
        self.assertIn("cost gate:", table)
        self.assertIn("best-in-class", table)

    def test_cli_subprocess_entry_point(self):
        fixture = ProbeFixture(tp=50, fn_clear=0, fn_jury=0, fp=0, tn=50)
        dataset_path, receipt_path, artifact_path = fixture.write(self.root)
        json_out = self.root / "packet.json"

        completed = subprocess.run(
            [sys.executable, "-m", "eval.bench.cascade_gate",
             "--artifact", str(artifact_path), "--dataset", str(dataset_path),
             "--receipt", str(receipt_path), "--json", str(json_out)],
            cwd=Path(__file__).parents[1], capture_output=True, text=True,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("cascade gate:", completed.stdout)


if __name__ == "__main__":
    unittest.main()
