#!/usr/bin/env python3
"""Offline workflow for externally supplied, self-attested human judgments."""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path

try:
    from . import label_audit_core as core
    from . import label_audit_stats as stats
except ImportError:  # Direct script execution.
    import label_audit_core as core
    import label_audit_stats as stats


REPO = Path(__file__).resolve().parents[2]


def _source(value: str) -> tuple[str, Path]:
    try:
        language, path = value.split("=", 1)
    except ValueError:
        raise argparse.ArgumentTypeError("source pool must be LANGUAGE=PATH") from None
    return core.canonical_language(language), Path(path)


def _json(path: Path) -> dict:
    return json.loads(core.read_bytes(Path(path), core.MAX_AUDIT_INPUT_BYTES, label="label audit JSON"))


def _coordinator(path: Path) -> dict:
    return core.load_coordinator(path)


def command_sample(args) -> int:
    artifacts = tuple(core.load_artifact(Path(path)) for path in args.artifact)
    pools = {language: core.load_source_pool(path, language) for language, path in args.source_pool}
    manifest = core.load_source_manifest(Path(args.source_manifest), REPO)
    selection = core.build_sample(artifacts, pools, audit_id=args.audit_id, seed=args.seed,
                                  source_manifest=manifest)
    rubric_sha = core.sha256_file(Path(args.rubric))
    key = secrets.token_bytes(32)
    outputs = core.write_blinded_packets(selection, Path(args.work_dir), blind_key=key,
                                         rubric_sha256=rubric_sha, repo=REPO)
    retained = sum(row.source_kind == "retained" for row in selection.selected)
    discarded = sum(row.source_kind == "discarded" for row in selection.selected)
    print(f"retained selected: {retained}")
    print(f"discarded selected: {discarded}")
    if selection.missing_discarded_languages:
        print("selection evidence unverified: " + ",".join(selection.missing_discarded_languages))
    print(f"coordinator: {outputs.coordinator}")
    print("HUMAN JUDGMENT REQUIRED")
    return 0


def command_check(args) -> int:
    annotations = core.load_annotations(Path(args.labels), Path(args.packet))
    print(f"{annotations.trust_status}: {len(annotations.judgments)} judgments; humanity not machine-verified")
    return 0


def command_third(args) -> int:
    coordinator = Path(args.coordinator)
    root = coordinator.parent
    coordinator_document = _coordinator(coordinator)
    first = core.load_annotations(Path(args.first), root / "annotator-a.packet.json")
    second = core.load_annotations(Path(args.second), root / "annotator-b.packet.json")
    source = core.load_packet(root / "adjudicator-source.packet.json")
    identities = {coordinator_document["coordinator_sha256"], first.coordinator_sha256,
                  second.coordinator_sha256, source["coordinator_sha256"]}
    if len(identities) != 1:
        raise ValueError("review inputs do not match coordinator identity")
    expected_ids = {row["blind_id"] for row in coordinator_document["items"]}
    if ({row["blind_id"] for row in first.judgments} != expected_ids or
            {row["blind_id"] for row in second.judgments} != expected_ids or
            {row["blind_id"] for row in source["items"]} != expected_ids):
        raise ValueError("review inputs do not exactly cover coordinator items")
    selected = core.select_third_review(first, second, rate=0.10, seed=args.seed)
    core.write_third_packet(root / "adjudicator-source.packet.json", selected, Path(args.out),
                            repo=REPO)
    print(f"third-review selected: {len(selected)}")
    print("HUMAN JUDGMENT REQUIRED")
    return 0


def _analysis(coordinator: dict, first: core.AnnotationSet, second: core.AnnotationSet,
              combined: core.CombinedLabels):
    by_id = {row["blind_id"]: row for row in coordinator["items"]}
    a = {row["blind_id"]: row for row in first.judgments}
    b = {row["blind_id"]: row for row in second.judgments}
    final = {label.blind_id: label for label in combined.labels}
    if set(by_id) != set(a) or set(a) != set(b) or set(b) != set(final):
        raise ValueError("human labels do not exactly cover coordinator items")
    decisive = [identifier for identifier in sorted(a)
                if a[identifier]["verdict"] != "insufficient-context" and
                b[identifier]["verdict"] != "insufficient-context"]
    labels_a = [a[i]["verdict"] for i in decisive]
    labels_b = [b[i]["verdict"] for i in decisive]
    strata = [f"{by_id[i]['language']}:{by_id[i]['stratum']}" for i in decisive]
    try:
        overall_kappa = stats.cohen_kappa(labels_a, labels_b)
    except ValueError:
        overall_kappa = None
    try:
        overall_lower = stats.bootstrap_kappa_ci(labels_a, labels_b, strata)[0]
    except ValueError:
        overall_lower = None
    language_kappa = {}
    for language in core.LANGUAGES:
        ids = [i for i in decisive if by_id[i]["language"] == language]
        try:
            language_kappa[language] = stats.cohen_kappa([a[i]["verdict"] for i in ids],
                                                         [b[i]["verdict"] for i in ids])
        except ValueError:
            language_kappa[language] = None
    results = [stats.AuditResult(row["language"], row["stratum"],
                                 final[row["blind_id"]].final_verdict != row["label"],
                                 row["inclusion_probability"])
               for row in coordinator["items"] if not final[row["blind_id"]].unresolved]
    overall_error = stats.weighted_error(results)
    language_estimates = {language: stats.weighted_error([row for row in results
                                                          if row.language == language])
                          for language in core.LANGUAGES}
    language_error = {language: estimate.upper
                      for language, estimate in language_estimates.items()}
    census_rates = []
    for language in core.LANGUAGES:
        for stratum in ("nominal_positive", "nominal_false_positive", "abstention"):
            rows = [row for row in results if row.language == language and row.stratum == stratum]
            if rows:
                census_rates.append(sum(row.label_error for row in rows) / len(rows))
    discarded = [row for row in results if row.stratum == "discarded_candidate"]
    inputs = stats.GateInputs(overall_kappa, overall_lower, language_kappa,
                              overall_error.upper, language_error, max(census_rates, default=0.0),
                              sum(row.label_error for row in discarded) / len(discarded) if discarded else 0.0,
                              sum(label.unresolved for label in combined.labels),
                              tuple(coordinator.get("missing_discarded_languages", [])), False)
    stratum_counts = {}
    probabilities = {}
    for row in coordinator["items"]:
        key = f"{row['language']}:{row['stratum']}"
        stratum_counts[key] = stratum_counts.get(key, 0) + 1
        probabilities.setdefault(key, set()).add(row["inclusion_probability"])
    evidence = {
        "coordinator_sha256": coordinator["coordinator_sha256"],
        "input_hashes": coordinator["input_hashes"],
        "selected_count": len(coordinator["items"]),
        "counts_by_language_stratum": stratum_counts,
        "inclusion_probabilities": {key: sorted(values) for key, values in probabilities.items()},
        "overall_kappa": overall_kappa,
        "overall_kappa_lower": overall_lower,
        "language_kappa": language_kappa,
        "overall_error": dataclass_dict(overall_error),
        "language_error": {language: dataclass_dict(estimate)
                           for language, estimate in language_estimates.items()},
        "uncertainty_rate": (sum(label.review_reason == "uncertainty"
                                 for label in combined.labels) / len(combined.labels)),
        "third_review_rate": (sum(label.review_reason is not None for label in combined.labels) /
                              len(combined.labels)),
        "thresholds": {"overall_kappa": 0.70, "overall_kappa_lower": 0.60,
                       "language_kappa": 0.60, "overall_error_upper": 0.05,
                       "language_error_upper": 0.10, "max_census_error": 0.05,
                       "discarded_usable_rate": 0.05},
    }
    return inputs, results, evidence


def dataclass_dict(value) -> dict:
    return {name: getattr(value, name) for name in ("point", "lower", "upper")}


def command_report(args) -> int:
    coordinator = _coordinator(Path(args.coordinator))
    root = Path(args.coordinator).parent
    first = core.load_annotations(Path(args.first), root / "annotator-a.packet.json")
    second = core.load_annotations(Path(args.second), root / "annotator-b.packet.json")
    third = core.load_annotations(Path(args.third), root / "annotator-c.packet.json")
    identities = {coordinator["coordinator_sha256"], first.coordinator_sha256,
                  second.coordinator_sha256, third.coordinator_sha256}
    if len(identities) != 1:
        raise ValueError("annotations do not match coordinator identity")
    selected = core.select_third_review(first, second, rate=0.10, seed=args.seed)
    combined = core.combine_human_labels(first, second, third, selected)
    inputs, _, evidence = _analysis(coordinator, first, second, combined)
    evidence["packet_sha256s"] = {"annotator_a": first.packet_sha256,
                                  "annotator_b": second.packet_sha256,
                                  "annotator_c": third.packet_sha256}
    evidence["annotation_sha256s"] = {
        "annotator_a": core.sha256_file(Path(args.first)),
        "annotator_b": core.sha256_file(Path(args.second)),
        "annotator_c": core.sha256_file(Path(args.third)),
    }
    gate = stats.evaluate_gate(inputs)
    by_id = {row["blind_id"]: row for row in coordinator["items"]}
    output = {"schema_version": 1, "audit_id": combined.audit_id,
              "rubric_sha256": combined.rubric_sha256,
              "coordinator_sha256": coordinator["coordinator_sha256"],
              "gate": {"status": gate.status, "qualification": gate.qualification,
                       "reasons": list(gate.reasons)},
              "evidence": evidence,
              "labels": [core.human_export_row(label, by_id[label.blind_id])
                         for label in combined.labels]}
    output["label_package_sha256"] = core.document_identity(output, "label_package_sha256")
    core._external_destination(Path(args.json_out), REPO)
    core._external_destination(Path(args.markdown_out), REPO)
    core.write_private_json(Path(args.json_out), output, repo=REPO)
    markdown = stats.render_audit_report(inputs, gate, evidence=evidence)
    core.write_private_text(Path(args.markdown_out), markdown, repo=REPO)
    print(gate.status)
    return 0


def command_rescore(args) -> int:
    result = core.rescore_overlay(core.load_artifact(Path(args.artifact)), _json(Path(args.overlay)))
    core.write_private_json(Path(args.out), result, repo=REPO)
    return 0


def command_split(args) -> int:
    coordinator = _coordinator(Path(args.coordinator))
    report = _json(Path(args.labels))
    if report.get("label_package_sha256") != core.document_identity(
            report, "label_package_sha256"):
        raise ValueError("human label package identity mismatch")
    if report.get("coordinator_sha256") != coordinator["coordinator_sha256"]:
        raise ValueError("human label package does not match coordinator identity")
    labels = core.CombinedLabels(report["audit_id"], report["rubric_sha256"], tuple(
        core.CombinedLabel(row["blind_id"], row["human_verdict"], row["human_category"],
                           False, None, ()) for row in report["labels"]))
    split_key = core.read_bytes(Path(args.split_key), 32, label="split key")
    result = core.split_by_repository(labels, coordinator["items"], split_key=split_key,
                                      development_fraction=args.development_fraction)
    output = Path(args.out_dir)
    if not output.is_absolute() or REPO == output.resolve() or REPO in output.resolve().parents:
        raise ValueError("split output must be an absolute directory outside the repository")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    by_id = {row["blind_id"]: row for row in coordinator["items"]}
    label_by_id = {label.blind_id: label for label in labels.labels}
    core.write_private_json(output / "development.json",
                            [core.human_export_row(label_by_id[i], by_id[i])
                             for i in result.development_ids], repo=REPO)
    core.write_private_json(output / "holdout.json",
                            [core.human_export_row(label_by_id[i], by_id[i])
                             for i in result.holdout_ids], repo=REPO)
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    sample = commands.add_parser("sample")
    sample.add_argument("--artifact", action="append", required=True)
    sample.add_argument("--source-pool", action="append", type=_source, default=[])
    sample.add_argument("--source-manifest",
                        default=str(REPO / "eval/bench/human-audit/source-pools.json"))
    sample.add_argument("--work-dir", required=True)
    sample.add_argument("--audit-id", default="evergreen-0.4.0-label-audit")
    sample.add_argument("--seed", type=int, default=20260713)
    sample.add_argument("--rubric", required=True)
    sample.set_defaults(handler=command_sample)
    check = commands.add_parser("check-labels")
    check.add_argument("--packet", required=True); check.add_argument("--labels", required=True)
    check.set_defaults(handler=command_check)
    third = commands.add_parser("make-third-review")
    for name in ("coordinator", "first", "second", "out"):
        third.add_argument(f"--{name}", required=True)
    third.add_argument("--seed", type=int, default=20260713); third.set_defaults(handler=command_third)
    report = commands.add_parser("report")
    for name in ("coordinator", "first", "second", "third", "json-out", "markdown-out"):
        report.add_argument(f"--{name}", required=True)
    report.add_argument("--seed", type=int, default=20260713); report.set_defaults(handler=command_report)
    rescore = commands.add_parser("rescore")
    for name in ("artifact", "overlay", "out"):
        rescore.add_argument(f"--{name}", required=True)
    rescore.set_defaults(handler=command_rescore)
    split = commands.add_parser("split")
    for name in ("coordinator", "labels", "split-key", "out-dir"):
        split.add_argument(f"--{name}", required=True)
    split.add_argument("--development-fraction", type=float, default=0.60)
    split.set_defaults(handler=command_split)
    return result


def main() -> int:
    args = parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
