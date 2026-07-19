#!/usr/bin/env python3
"""Offline, fail-closed quality-and-cost gate over one completed v3 probe artifact.

Consumes a completed resolver-v3 probe artifact, the exact 100-row probe dataset and receipt
that bind it (Task 1's make_probe output), and an optional adjudicated label overlay covering
those same 100 ids. Emits a deterministic decision packet (JSON + a human-readable table) and
exits 0 only when every declared gate passes:

  QUALITY: precision AND recall AND F1 each >= 0.80 on the 50/50 probe, evaluated on adjudicated
  labels. Adjudication is required for this gate; if none is supplied the gate fails rather than
  silently falling back to nominal labels.

  COST: >= 70% fewer ACTUAL provider attempts than a full-v2 counterfactual, with retries counted
  (never hidden). The counterfactual prefers a measured same-row full-v2 artifact; lacking one, it
  falls back to a conservative *projected* reconstruction (six logical calls per row minimum, plus
  optional historical escalation/synthesis rates for a closest same-judge lane) and can only pass
  with explicit human acceptance (--accept-projected-cost-gate) — a projection never manufactures a
  pass on its own.

Calls no model, network, or subprocess. Python 3 stdlib only.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

try:
    from . import artifact, metrics, report
    from .split_manifest import _loads_strict
except ImportError:  # Direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import artifact
    import metrics
    import report
    from split_manifest import _loads_strict


PRECISION_THRESHOLD = 0.80
RECALL_THRESHOLD = 0.80
F1_THRESHOLD = 0.80
COST_REDUCTION_THRESHOLD = 0.70

PROBE_POSITIVE_COUNT = 50
PROBE_CONTROL_COUNT = 50

# Full-v2 jury minimum per row: snap + challenge + 3 prongs + blindspot (see trial._judge_full).
FULL_JURY_BASE_LOGICAL_CALLS = 6
FULL_JURY_ESCALATED_PRONGS_CALLS = 3  # a failed plurality re-runs all 3 prongs at the strong tier
FULL_JURY_SYNTHESIS_CALLS = 1

MAX_ARTIFACT_BYTES = artifact.MAX_ARTIFACT_BYTES
MAX_DATASET_BYTES = 8 * 1024 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_OVERLAY_BYTES = 1024 * 1024
MAX_HISTORICAL_LANE_BYTES = 64 * 1024


def _hex64(value):
    return report._is_hex(value, {64})


def _parse_jsonl(payload, label):
    rows = []
    for line in payload.splitlines():
        if not line.strip():
            continue
        try:
            item = _loads_strict(line)
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"{label} is not valid JSONL") from error
        if not isinstance(item, dict):
            raise ValueError(f"{label} row must be a JSON object")
        rows.append(item)
    return rows


def _read_json(path, max_bytes, label):
    payload = artifact.read_bytes(Path(path), max_bytes, label=label)
    try:
        document = _loads_strict(payload)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    return payload, document


# -- probe binding (dataset + receipt) ---------------------------------------------------------

def load_receipt(path):
    """Parse and validate a make_probe receipt; return (receipt, receipt_sha256)."""
    payload, receipt = _read_json(path, MAX_RECEIPT_BYTES, "probe receipt")
    receipt_sha256 = hashlib.sha256(payload).hexdigest()
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        raise ValueError("unsupported probe receipt schema")
    rows = receipt.get("rows")
    if (not isinstance(rows, list) or not rows or
            any(not isinstance(row, dict) or set(row) != {"id", "label"} or
                not isinstance(row.get("id"), str) or not row["id"] or
                row.get("label") not in ("consistent", "inconsistent") for row in rows)):
        raise ValueError("probe receipt rows are invalid")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("probe receipt contains duplicate ids")
    positive_count = sum(1 for row in rows if row["label"] == "inconsistent")
    control_count = sum(1 for row in rows if row["label"] == "consistent")
    if (receipt.get("positive_count") != positive_count or
            receipt.get("control_count") != control_count):
        raise ValueError("probe receipt declared counts do not match its rows")
    if positive_count != PROBE_POSITIVE_COUNT or control_count != PROBE_CONTROL_COUNT:
        raise ValueError(
            f"probe receipt is not a {PROBE_POSITIVE_COUNT}/{PROBE_CONTROL_COUNT} probe "
            f"(found {positive_count}/{control_count})"
        )
    if not _hex64(receipt.get("output_dataset_sha256")):
        raise ValueError("probe receipt output_dataset_sha256 is invalid")
    if not _hex64(receipt.get("parent_dataset_sha256")):
        raise ValueError("probe receipt parent_dataset_sha256 is invalid")
    return receipt, receipt_sha256


def bind_probe_dataset(dataset_path, receipt):
    """Bind the on-disk probe dataset to its receipt; return (dataset_sha256, rows_by_id)."""
    payload = artifact.read_bytes(Path(dataset_path), MAX_DATASET_BYTES, label="probe dataset")
    dataset_sha256 = hashlib.sha256(payload).hexdigest()
    if dataset_sha256 != receipt["output_dataset_sha256"]:
        raise ValueError("probe dataset sha256 does not match the receipt")
    dataset_rows = _parse_jsonl(payload, "probe dataset")
    rows_by_id = {}
    for row in dataset_rows:
        pair_id = row.get("id")
        if not isinstance(pair_id, str) or not pair_id:
            raise ValueError("probe dataset row id must be a non-empty string")
        if pair_id in rows_by_id:
            raise ValueError(f"duplicate id in probe dataset: {pair_id!r}")
        rows_by_id[pair_id] = row
    receipt_by_id = {row["id"]: row["label"] for row in receipt["rows"]}
    if set(rows_by_id) != set(receipt_by_id):
        raise ValueError("probe dataset ids do not match the receipt")
    for pair_id, label in receipt_by_id.items():
        if rows_by_id[pair_id].get("label") != label:
            raise ValueError(f"probe dataset label for {pair_id!r} does not match the receipt")
    return dataset_sha256, rows_by_id


# -- v3 artifact loading and binding -----------------------------------------------------------

def _load_artifact_document(path, label):
    """Read, hash, and structurally validate one benchmark artifact envelope (any resolver)."""
    payload, document = _read_json(path, MAX_ARTIFACT_BYTES, label)
    artifact_sha256 = hashlib.sha256(payload).hexdigest()
    if isinstance(document, list):
        raise ValueError("legacy artifact provenance is unknown; gate refused")
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError(f"unsupported {label} schema")
    metadata = document.get("metadata")
    report._validate_metadata(metadata)
    rows = document.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"{label} rows must be a list")
    return artifact_sha256, metadata, rows


def load_probe_artifact(path):
    """Parse, validate, and return (artifact_sha256, metadata, rows) for one v3 artifact."""
    artifact_sha256, metadata, rows = _load_artifact_document(path, "v3 probe artifact")
    if metadata.get("settings", {}).get("resolver") != "v3":
        raise ValueError("v3 probe artifact settings do not declare resolver v3")
    seen = set()
    for row in rows:
        artifact.validate_benchmark_row(row, require_result=True)
        if row["id"] in seen:
            raise ValueError(f"duplicate pair id in v3 probe artifact: {row['id']!r}")
        seen.add(row["id"])
    return artifact_sha256, metadata, rows


def bind_artifact_to_probe(metadata, rows, dataset_sha256, dataset_by_id):
    """Fail closed unless the artifact's rows are exactly, completely the bound 100-row probe."""
    if metadata["dataset"]["sha256"] != dataset_sha256:
        raise ValueError("v3 probe artifact dataset sha256 does not match the bound probe dataset")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate pair id in v3 probe artifact")
    missing = set(dataset_by_id) - set(ids)
    extra = set(ids) - set(dataset_by_id)
    if missing or extra:
        raise ValueError(
            "v3 probe artifact coverage does not match the probe's "
            f"{len(dataset_by_id)} ids (missing {len(missing)}, unexpected {len(extra)})"
        )
    for row in rows:
        probe_row = dataset_by_id[row["id"]]
        if row.get("label") != probe_row.get("label"):
            raise ValueError(f"artifact label for {row['id']!r} does not match the probe dataset")
        if row.get("category") != probe_row.get("category"):
            raise ValueError(
                f"artifact category for {row['id']!r} does not match the probe dataset"
            )
        if row.get("language") != probe_row.get("language"):
            raise ValueError(
                f"artifact language for {row['id']!r} does not match the probe dataset"
            )
        got = row.get("got") or {}
        if got.get("final_status") != "complete":
            raise ValueError(f"v3 probe artifact row {row['id']!r} did not complete (abstention)")


# -- adjudicated label overlay ------------------------------------------------------------------

def load_adjudicated_overlay(path, probe_ids):
    """Load an adjudication overlay; it must cover exactly the probe's ids, no more, no fewer."""
    _payload, document = _read_json(path, MAX_OVERLAY_BYTES, "adjudicated label overlay")
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("unsupported adjudicated label overlay schema")
    rows = document.get("rows")
    if (not isinstance(rows, list) or
            any(not isinstance(row, dict) or set(row) != {"id", "label"} or
                not isinstance(row.get("id"), str) or not row["id"] or
                row.get("label") not in ("consistent", "inconsistent") for row in rows)):
        raise ValueError("adjudicated label overlay rows are invalid")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("adjudicated label overlay contains duplicate ids")
    overlay = {row["id"]: row["label"] for row in rows}
    if set(overlay) != set(probe_ids):
        raise ValueError(
            "adjudicated label overlay must cover exactly the probe's "
            f"{len(probe_ids)} ids, no more and no fewer"
        )
    return overlay


def _relabeled(rows, overlay):
    return [{**row, "label": overlay[row["id"]]} for row in rows]


# -- quality: nominal and adjudicated metrics ----------------------------------------------------

def quality_metrics(rows):
    """Score one label view of the probe's rows via the shared metrics module (never reinvented)."""
    return metrics.score(metrics.rows_from_transcript(rows))


def auto_clear_false_negatives(rows, overlay=None):
    """Ids the cheap screen auto-cleared (route=clear, predicted consistent) whose true label —
    adjudicated when supplied, else nominal — is inconsistent: drift the screen missed and never
    escalated to the jury."""
    found = []
    for row in rows:
        execution = ((row.get("got") or {}).get("execution")) or {}
        if execution.get("route") != "clear":
            continue
        label = overlay[row["id"]] if overlay is not None else row.get("label")
        if label == "inconsistent":
            found.append(row["id"])
    return sorted(found)


def evaluate_quality_gate(nominal_metrics, adjudicated_metrics, thresholds):
    """The quality gate is declared over ADJUDICATED labels; nominal is reported, never gated on."""
    precision_threshold, recall_threshold, f1_threshold = thresholds
    base = {
        "passed": False,
        "precision_threshold": precision_threshold,
        "recall_threshold": recall_threshold,
        "f1_threshold": f1_threshold,
        "precision": None, "recall": None, "f1": None,
    }
    if adjudicated_metrics is None:
        return {
            **base,
            "reason": "adjudicated labels are required for the quality gate but none were supplied",
        }
    precision = adjudicated_metrics["precision"]
    recall = adjudicated_metrics["recall"]
    f1 = adjudicated_metrics["f1"]
    if precision is None or recall is None or f1 is None:
        return {
            **base,
            "reason": "adjudicated metrics are unavailable (core set lacks both label classes)",
        }
    passed = (precision >= precision_threshold and recall >= recall_threshold and
              f1 >= f1_threshold)
    return {
        "passed": passed, "reason": None,
        "precision_threshold": precision_threshold, "recall_threshold": recall_threshold,
        "f1_threshold": f1_threshold,
        "precision": precision, "recall": recall, "f1": f1,
    }


# -- cost: actual v3 spend and the full-v2 counterfactual ---------------------------------------

def actual_execution_summary(rows):
    """The v3 side of the cost gate: the deterministic got.execution ledger, never re-derived."""
    accounting = report.execution_accounting(rows)
    if accounting["provider_attempts"] is None:
        raise ValueError("v3 probe artifact rows are missing the required execution ledger")
    return accounting


def load_historical_lane_summary(path):
    _payload, document = _read_json(path, MAX_HISTORICAL_LANE_BYTES, "historical lane summary")
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("unsupported historical lane summary schema")
    for key in ("escalated_prong_rate", "synthesis_rate"):
        value = document.get(key)
        if (not isinstance(value, (int, float)) or isinstance(value, bool) or
                not 0.0 <= value <= 1.0):
            raise ValueError(f"historical lane summary {key} must be a number between 0 and 1")
    if "lane_id" in document and not isinstance(document["lane_id"], str):
        raise ValueError("historical lane summary lane_id must be a string")
    return document


def measured_full_v2_counterfactual(rows, probe_ids):
    """A same-row full-v2 artifact's ACTUAL attempts, when its rows carry an execution ledger.

    Returns None (never raises for this alone) when the supplied artifact has no ledger, so the
    caller can fall back to a conservative projection rather than manufacture a measured claim.
    """
    ids = [row.get("id") for row in rows]
    if len(set(ids)) != len(ids) or set(ids) != set(probe_ids):
        raise ValueError(
            "full-v2 counterfactual artifact does not cover exactly the probe's "
            f"{len(probe_ids)} ids"
        )
    for row in rows:
        artifact.validate_benchmark_row(row, require_result=True)
        if (row.get("got") or {}).get("final_status") != "complete":
            raise ValueError(f"full-v2 counterfactual row {row['id']!r} did not complete")
    accounting = report.execution_accounting(rows)
    if accounting["provider_attempts"] is None:
        return None
    return {
        "basis": "measured",
        "provider_attempts": accounting["provider_attempts"],
        "logical_calls": accounting["logical_calls"],
        "retries": accounting["retries"],
    }


def projected_full_v2_counterfactual(row_count, historical_lane=None):
    """Conservative logical-call reconstruction: 6/row floor, +3 for escalated prongs, +1 for
    synthesis, at the historical lane's observed rates when supplied. Never carries actual
    provider attempts — a projection is a lower/expected bound on logical calls, not a measurement.
    """
    floor = row_count * FULL_JURY_BASE_LOGICAL_CALLS
    ceiling = row_count * (
        FULL_JURY_BASE_LOGICAL_CALLS + FULL_JURY_ESCALATED_PRONGS_CALLS + FULL_JURY_SYNTHESIS_CALLS
    )
    expected = None
    if historical_lane is not None:
        expected = row_count * (
            FULL_JURY_BASE_LOGICAL_CALLS +
            FULL_JURY_ESCALATED_PRONGS_CALLS * historical_lane["escalated_prong_rate"] +
            FULL_JURY_SYNTHESIS_CALLS * historical_lane["synthesis_rate"]
        )
    return {
        "basis": "projected",
        "provider_attempts": None,
        "logical_calls_floor": floor,
        "logical_calls_ceiling": ceiling,
        "logical_calls_expected": expected,
        "historical_lane_id": historical_lane.get("lane_id") if historical_lane else None,
        "escalated_prong_rate": historical_lane.get("escalated_prong_rate") if historical_lane
        else None,
        "synthesis_rate": historical_lane.get("synthesis_rate") if historical_lane else None,
    }


def evaluate_cost_gate(actual, counterfactual, threshold, accept_projected):
    """A projection may only pass with explicit human acceptance; it never manufactures one."""
    v3_attempts = actual["provider_attempts"]
    if counterfactual["basis"] == "measured":
        denominator = counterfactual["provider_attempts"]
        reduction = 1 - (v3_attempts / denominator) if denominator else None
        passed = reduction is not None and reduction >= threshold
        return {
            "basis": "measured", "passed": passed, "threshold": threshold,
            "reduction": reduction, "requires_human_acceptance": False, "accepted": None,
        }
    floor = counterfactual["logical_calls_floor"]
    ceiling = counterfactual["logical_calls_ceiling"]
    expected = counterfactual["logical_calls_expected"]
    reduction_conservative = 1 - (v3_attempts / floor) if floor else None
    reduction_optimistic = 1 - (v3_attempts / ceiling) if ceiling else None
    reduction_expected = 1 - (v3_attempts / expected) if expected else None
    meets_threshold = reduction_conservative is not None and reduction_conservative >= threshold
    return {
        "basis": "projected", "threshold": threshold,
        "reduction_conservative": reduction_conservative,
        "reduction_expected": reduction_expected,
        "reduction_optimistic": reduction_optimistic,
        "meets_threshold_conservative": meets_threshold,
        "requires_human_acceptance": True,
        "accepted": bool(accept_projected),
        "passed": bool(accept_projected) and meets_threshold,
    }


# -- decision packet ------------------------------------------------------------------------------

def build_decision_packet(
    artifact_path, dataset_path, receipt_path, *, adjudicated_path=None,
    full_v2_artifact_path=None, historical_lane_path=None, accept_projected_cost_gate=False,
    precision_threshold=PRECISION_THRESHOLD, recall_threshold=RECALL_THRESHOLD,
    f1_threshold=F1_THRESHOLD, cost_reduction_threshold=COST_REDUCTION_THRESHOLD,
):
    """Build the full offline decision packet. Raises ValueError on any structural/binding
    problem (mismatched hashes/ids, incomplete coverage, abstentions) — the caller turns that
    into a fail-closed packet rather than letting an exception escape as a bare traceback."""
    receipt, receipt_sha256 = load_receipt(receipt_path)
    dataset_sha256, dataset_by_id = bind_probe_dataset(dataset_path, receipt)
    probe_ids = set(dataset_by_id)

    artifact_sha256, artifact_metadata, rows = load_probe_artifact(artifact_path)
    bind_artifact_to_probe(artifact_metadata, rows, dataset_sha256, dataset_by_id)

    overlay = None
    if adjudicated_path is not None:
        overlay = load_adjudicated_overlay(adjudicated_path, probe_ids)

    nominal_metrics = quality_metrics(rows)
    adjudicated_metrics = quality_metrics(_relabeled(rows, overlay)) if overlay else None
    quality_gate = evaluate_quality_gate(
        nominal_metrics, adjudicated_metrics,
        (precision_threshold, recall_threshold, f1_threshold),
    )

    auto_clear_fn_nominal = auto_clear_false_negatives(rows)
    auto_clear_fn_adjudicated = (
        auto_clear_false_negatives(rows, overlay) if overlay is not None else None
    )

    actual = actual_execution_summary(rows)
    budget = artifact_metadata.get("settings", {}).get("max_provider_attempts")
    headroom = (
        budget - actual["provider_attempts"]
        if isinstance(budget, int) and not isinstance(budget, bool) else None
    )

    counterfactual = None
    if full_v2_artifact_path is not None:
        _full_v2_sha256, _full_v2_metadata, full_v2_rows = load_full_v2_artifact(
            full_v2_artifact_path
        )
        counterfactual = measured_full_v2_counterfactual(full_v2_rows, probe_ids)
    historical_lane = (
        load_historical_lane_summary(historical_lane_path) if historical_lane_path else None
    )
    if counterfactual is None:
        counterfactual = projected_full_v2_counterfactual(len(rows), historical_lane)
    cost_gate = evaluate_cost_gate(
        actual, counterfactual, cost_reduction_threshold, accept_projected_cost_gate
    )

    passed = bool(quality_gate["passed"] and cost_gate["passed"])
    packet = {
        "schema_version": 1,
        "passed": passed,
        "probe": {
            "positive_count": receipt["positive_count"], "control_count": receipt["control_count"],
            "dataset_sha256": dataset_sha256, "receipt_sha256": receipt_sha256,
            "parent_dataset_sha256": receipt["parent_dataset_sha256"],
        },
        "artifact": {"sha256": artifact_sha256, "row_count": len(rows)},
        "judge": {
            "identity_sha256": artifact_metadata["judge"]["sha256"],
            "provider": artifact_metadata.get("provider"),
            "models": artifact_metadata.get("settings", {}).get("models"),
        },
        "quality": {"nominal": nominal_metrics, "adjudicated": adjudicated_metrics},
        "auto_clear_false_negatives": {
            "nominal": auto_clear_fn_nominal, "adjudicated": auto_clear_fn_adjudicated,
        },
        "execution": {
            "escalation_rate": actual["escalation_rate"],
            "logical_calls": actual["logical_calls"],
            "provider_attempts": actual["provider_attempts"],
            "retries": actual["retries"],
            "attempts_per_row": actual["attempts_per_row"],
            "budget": budget, "budget_headroom": headroom,
        },
        "counterfactual": counterfactual,
        "gates": {"quality": quality_gate, "cost": cost_gate},
    }
    return packet


def load_full_v2_artifact(path):
    """Like load_probe_artifact, but for the full-v2 counterfactual side, which is resolver v2."""
    artifact_sha256, metadata, rows = _load_artifact_document(
        path, "full-v2 counterfactual artifact"
    )
    if metadata.get("settings", {}).get("resolver") != "v2":
        raise ValueError("full-v2 counterfactual artifact settings do not declare resolver v2")
    return artifact_sha256, metadata, rows


# -- rendering --------------------------------------------------------------------------------

def _fmt(value, spec="{:.3f}"):
    return "unavailable" if value is None else spec.format(value)


def render_table(packet):
    quality = packet["gates"]["quality"]
    cost = packet["gates"]["cost"]
    execution = packet["execution"]
    lines = [
        f"cascade gate: {'PASS' if packet['passed'] else 'FAIL'}",
        "",
        f"artifact sha256={packet['artifact']['sha256']} rows={packet['artifact']['row_count']}",
        f"probe {packet['probe']['positive_count']}+{packet['probe']['control_count']} "
        f"dataset_sha256={packet['probe']['dataset_sha256']} "
        f"receipt_sha256={packet['probe']['receipt_sha256']}",
        f"judge identity_sha256={packet['judge']['identity_sha256']} "
        f"provider={packet['judge']['provider']} models={packet['judge']['models']}",
        "",
        f"quality gate: {'PASS' if quality['passed'] else 'FAIL'}"
        + (f" ({quality['reason']})" if quality.get("reason") else ""),
        f"  adjudicated  precision={_fmt(quality['precision'])} (>= {quality['precision_threshold']:.2f})"
        f"  recall={_fmt(quality['recall'])} (>= {quality['recall_threshold']:.2f})"
        f"  f1={_fmt(quality['f1'])} (>= {quality['f1_threshold']:.2f})",
        f"  nominal      precision={_fmt(packet['quality']['nominal']['precision'])}"
        f"  recall={_fmt(packet['quality']['nominal']['recall'])}"
        f"  f1={_fmt(packet['quality']['nominal']['f1'])}",
        f"  auto-clear false negatives: nominal={packet['auto_clear_false_negatives']['nominal']} "
        f"adjudicated={packet['auto_clear_false_negatives']['adjudicated']}",
        "",
        f"cost gate: {'PASS' if cost['passed'] else 'FAIL'} (basis={cost['basis']})",
        f"  actual provider_attempts={execution['provider_attempts']} "
        f"logical_calls={execution['logical_calls']} retries={execution['retries']} "
        f"attempts_per_row={_fmt(execution['attempts_per_row'], '{:.2f}')}",
        f"  budget={execution['budget']} headroom={execution['budget_headroom']} "
        f"escalation_rate={_fmt(execution['escalation_rate'], '{:.1%}')}",
    ]
    if cost["basis"] == "measured":
        lines.append(
            f"  counterfactual measured provider_attempts="
            f"{packet['counterfactual']['provider_attempts']} "
            f"reduction={_fmt(cost['reduction'], '{:.1%}')} (>= {cost['threshold']:.0%})"
        )
    else:
        lines.extend([
            f"  counterfactual projected reduction: conservative={_fmt(cost['reduction_conservative'], '{:.1%}')}"
            f" expected={_fmt(cost['reduction_expected'], '{:.1%}')}"
            f" optimistic={_fmt(cost['reduction_optimistic'], '{:.1%}')} (>= {cost['threshold']:.0%})",
            f"  requires human acceptance: {cost['requires_human_acceptance']} "
            f"accepted={cost['accepted']}",
        ])
    lines.append("")
    lines.append(
        "This gate emits PASS/FAIL on the declared thresholds only; it does not certify the "
        "probe best-in-class and does not authorize a larger run."
    )
    return "\n".join(lines) + "\n"


# -- CLI ----------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True, help="the v3 probe artifact")
    parser.add_argument("--dataset", type=Path, required=True, help="the 100-row probe dataset")
    parser.add_argument("--receipt", type=Path, required=True, help="the probe's make_probe receipt")
    parser.add_argument("--adjudicated", type=Path, default=None,
                         help="optional adjudicated label overlay covering exactly the probe ids")
    parser.add_argument("--full-v2-artifact", type=Path, default=None,
                         help="optional measured same-row full-v2 counterfactual artifact")
    parser.add_argument("--historical-lane-summary", type=Path, default=None,
                         help="optional escalated-prong/synthesis rates for the projected counterfactual")
    parser.add_argument("--accept-projected-cost-gate", action="store_true",
                         help="explicitly accept a projected (non-measured) cost counterfactual")
    parser.add_argument("--precision-threshold", type=float, default=PRECISION_THRESHOLD)
    parser.add_argument("--recall-threshold", type=float, default=RECALL_THRESHOLD)
    parser.add_argument("--f1-threshold", type=float, default=F1_THRESHOLD)
    parser.add_argument("--cost-reduction-threshold", type=float, default=COST_REDUCTION_THRESHOLD)
    parser.add_argument("--json", type=Path, required=True, help="where to write the decision packet")
    parser.add_argument("--table", type=Path, default=None, help="optional human-readable table path")
    args = parser.parse_args(argv)

    try:
        packet = build_decision_packet(
            args.artifact, args.dataset, args.receipt,
            adjudicated_path=args.adjudicated, full_v2_artifact_path=args.full_v2_artifact,
            historical_lane_path=args.historical_lane_summary,
            accept_projected_cost_gate=args.accept_projected_cost_gate,
            precision_threshold=args.precision_threshold, recall_threshold=args.recall_threshold,
            f1_threshold=args.f1_threshold, cost_reduction_threshold=args.cost_reduction_threshold,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        packet = {"schema_version": 1, "passed": False, "error": str(error)}

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n")
    table = (
        render_table(packet) if "error" not in packet
        else f"cascade gate: FAIL (structural error: {packet['error']})\n"
    )
    print(table, end="")
    if args.table is not None:
        args.table.parent.mkdir(parents=True, exist_ok=True)
        args.table.write_text(table)
    return 0 if packet.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
