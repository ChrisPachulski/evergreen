#!/usr/bin/env python3
"""Offline workflow for externally supplied, self-attested human judgments."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    document = _json(path)
    if document.get("schema_version") != 1 or not isinstance(document.get("items"), list):
        raise ValueError("coordinator document is invalid")
    return document


def command_sample(args) -> int:
    artifacts = tuple(core.load_artifact(Path(path)) for path in args.artifact)
    pools = {language: core.load_source_pool(path, language) for language, path in args.source_pool}
    selection = core.build_sample(artifacts, pools, audit_id=args.audit_id, seed=args.seed)
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
    first = core.load_annotations(Path(args.first), root / "annotator-a.packet.json")
    second = core.load_annotations(Path(args.second), root / "annotator-b.packet.json")
    selected = core.select_third_review(first, second, rate=0.10, seed=args.seed)
    core.write_third_packet(root / "adjudicator-source.packet.json", selected, Path(args.out))
    print(f"third-review selected: {len(selected)}")
    print("HUMAN JUDGMENT REQUIRED")
    return 0


def _analysis(coordinator: dict, first: core.AnnotationSet, second: core.AnnotationSet,
              combined: core.CombinedLabels):
    by_id = {row["blind_id"]: row for row in coordinator["items"]}
    a = {row["blind_id"]: row for row in first.judgments}
    b = {row["blind_id"]: row for row in second.judgments}
    decisive = [identifier for identifier in sorted(a)
                if a[identifier]["verdict"] != "insufficient-context" and
                b[identifier]["verdict"] != "insufficient-context"]
    labels_a = [a[i]["verdict"] for i in decisive]
    labels_b = [b[i]["verdict"] for i in decisive]
    strata = [f"{by_id[i]['language']}:{by_id[i]['stratum']}" for i in decisive]
    overall_kappa = stats.cohen_kappa(labels_a, labels_b)
    overall_lower = stats.bootstrap_kappa_ci(labels_a, labels_b, strata)[0]
    language_kappa = {}
    for language in core.LANGUAGES:
        ids = [i for i in decisive if by_id[i]["language"] == language]
        language_kappa[language] = stats.cohen_kappa([a[i]["verdict"] for i in ids],
                                                     [b[i]["verdict"] for i in ids])
    final = {label.blind_id: label for label in combined.labels}
    results = [stats.AuditResult(row["language"], row["stratum"],
                                 final[row["blind_id"]].final_verdict != row["label"],
                                 row["inclusion_probability"])
               for row in coordinator["items"] if not final[row["blind_id"]].unresolved]
    overall_error = stats.weighted_error(results)
    language_error = {language: stats.weighted_error([row for row in results
                                                      if row.language == language]).upper
                      for language in core.LANGUAGES}
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
    return inputs, results


def command_report(args) -> int:
    coordinator = _coordinator(Path(args.coordinator))
    root = Path(args.coordinator).parent
    first = core.load_annotations(Path(args.first), root / "annotator-a.packet.json")
    second = core.load_annotations(Path(args.second), root / "annotator-b.packet.json")
    third = core.load_annotations(Path(args.third), root / "annotator-c.packet.json")
    selected = core.select_third_review(first, second, rate=0.10, seed=args.seed)
    combined = core.combine_human_labels(first, second, third, selected)
    inputs, _ = _analysis(coordinator, first, second, combined)
    gate = stats.evaluate_gate(inputs)
    output = {"schema_version": 1, "audit_id": combined.audit_id,
              "rubric_sha256": combined.rubric_sha256,
              "gate": {"status": gate.status, "qualification": gate.qualification,
                       "reasons": list(gate.reasons)},
              "labels": [{"blind_id": label.blind_id, "final_verdict": label.final_verdict,
                          "final_category": label.final_category, "unresolved": label.unresolved}
                         for label in combined.labels]}
    core._atomic_json(Path(args.json_out), output)
    markdown = stats.render_audit_report(inputs, gate)
    Path(args.markdown_out).write_text(markdown)
    print(gate.status)
    return 0


def command_rescore(args) -> int:
    result = core.rescore_overlay(core.load_artifact(Path(args.artifact)), _json(Path(args.overlay)))
    core._atomic_json(Path(args.out), result)
    return 0


def command_split(args) -> int:
    coordinator = _coordinator(Path(args.coordinator))
    report = _json(Path(args.labels))
    labels = core.CombinedLabels(report["audit_id"], report["rubric_sha256"], tuple(
        core.CombinedLabel(row["blind_id"], row["final_verdict"], row["final_category"],
                           row["unresolved"], None, ()) for row in report["labels"]))
    split_key = core.read_bytes(Path(args.split_key), 32, label="split key")
    result = core.split_by_repository(labels, coordinator["items"], split_key=split_key,
                                      development_fraction=args.development_fraction)
    output = Path(args.out_dir)
    if not output.is_absolute() or REPO == output.resolve() or REPO in output.resolve().parents:
        raise ValueError("split output must be an absolute directory outside the repository")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    by_id = {row["blind_id"]: row for row in coordinator["items"]}
    core._atomic_json(output / "development.json", [by_id[i] for i in result.development_ids])
    core._atomic_json(output / "holdout.json", [by_id[i] for i in result.holdout_ids])
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    sample = commands.add_parser("sample")
    sample.add_argument("--artifact", action="append", required=True)
    sample.add_argument("--source-pool", action="append", type=_source, default=[])
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
