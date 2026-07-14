"""Pure, fail-closed policy and grade evaluation."""

import json
import math
import re
from collections.abc import Mapping
from types import MappingProxyType


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
POLICY_FIELDS = {
    "schema_version", "kind", "policy_id", "required_categories", "category_gates",
    "required_languages", "artifact_roles", "detector", "required_command_ids",
    "forbidden_path_rules", "external_state_names", "limits",
}
EVIDENCE_FIELDS = {
    "schema_version", "kind", "evaluated_release", "subject", "policy",
    "required_categories", "required_languages", "detector", "peers", "changed_paths",
    "subject_executables", "external_states",
}
GATES = {
    "detector_quality": ("detector_metrics",),
    "same_corpus_comparison": ("peer_applicability",),
    "trust_security": ("trust_matrix",),
    "claude_self_application": ("claude_active_installation",),
    "codex_self_application": ("codex_active_installation",),
    "documentation_release_honesty": ("documentation_claims",),
    "reproducibility_ci": ("macos_linux",),
    "cleanup": ("clean_tree",),
}
THRESHOLD_FLOORS = {
    "provider_completion": 0.99,
    "semantic_coverage": 0.99,
    "precision": 0.80,
    "recall": 0.80,
    "f1": 0.80,
    "specificity": 0.98,
}
LOWER_BOUND_FLOORS = {"precision": 0.70, "recall": 0.70, "f1": 0.70}
EXTERNAL_STATES = {"verified", "unverified", "not-applicable"}
HEX = re.compile(r"[0-9a-f]+")
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


class GradeError(ValueError):
    """The policy or evidence cannot safely produce a grade."""


class _Object(dict):
    __slots__ = ("duplicates",)


def _object(pairs):
    value = _Object()
    duplicates = []
    for key, item in pairs:
        if key in value:
            duplicates.append(key)
        else:
            value[key] = item
    value.duplicates = tuple(duplicates)
    return value


def _constant(value):
    raise GradeError(f"JSON number must be finite: {value}")


def _load(payload):
    if not isinstance(payload, (bytes, bytearray, str)):
        raise GradeError("JSON input must be bytes or text")
    try:
        value = json.loads(payload, object_pairs_hook=_object, parse_constant=_constant)
    except GradeError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError):
        raise GradeError("invalid JSON") from None
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, _Object) and item.duplicates:
            raise GradeError(f"duplicate JSON key: {item.duplicates[0]}")
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise GradeError("JSON numbers must be finite")
    return value


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _exact_object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise GradeError(f"{label} fields are invalid")


def _hex(value, label, lengths=(40, 64)):
    if not isinstance(value, str) or len(value) not in lengths or not HEX.fullmatch(value):
        raise GradeError(f"{label} is invalid")
    return value


def _identity(value, label):
    _exact_object(value, {"commit", "tree"}, label)
    return {
        "commit": _hex(value["commit"], f"{label} commit"),
        "tree": _hex(value["tree"], f"{label} tree"),
    }


def _string_list(value, label, *, nonempty=True):
    if (not isinstance(value, list) or (nonempty and not value) or
            any(not isinstance(item, str) or not item for item in value) or
            len(set(value)) != len(value)):
        raise GradeError(f"{label} are invalid")
    return tuple(value)


def load_policy(payload):
    """Load and recursively freeze the closed v1 grade policy."""
    policy = _load(payload)
    _exact_object(policy, POLICY_FIELDS, "policy")
    if (type(policy["schema_version"]) is not int or policy["schema_version"] != 1 or
            policy["kind"] != "evergreen-a-grade-policy" or
            policy["policy_id"] != "a-grade-v1"):
        raise GradeError("policy identity is invalid")
    if _string_list(policy["required_categories"], "policy categories") != CATEGORIES:
        raise GradeError("policy categories are invalid")
    if _string_list(policy["required_languages"], "policy languages") != LANGUAGES:
        raise GradeError("policy languages are invalid")
    if not isinstance(policy["category_gates"], dict) or set(policy["category_gates"]) != set(CATEGORIES):
        raise GradeError("policy category gates are invalid")
    for category in CATEGORIES:
        if tuple(policy["category_gates"][category]) != GATES[category]:
            raise GradeError("policy category gates are invalid")

    detector = policy["detector"]
    _exact_object(
        detector, {
            "minimum_negative", "minimum_positive", "prevalence", "thresholds",
            "confidence_level", "confidence_cluster", "lower_bound_thresholds",
        },
        "policy detector",
    )
    for name in ("minimum_negative", "minimum_positive"):
        if type(detector[name]) is not int or detector[name] < 100:
            raise GradeError(f"policy {name} is below trusted v1 floor")
    prevalence = detector["prevalence"]
    if type(prevalence) not in (int, float) or prevalence != 0.10:
        raise GradeError("policy prevalence must be trusted v1 value 0.1")
    if (type(detector["confidence_level"]) not in (int, float) or
            detector["confidence_level"] != 0.95 or
            detector["confidence_cluster"] != "repository"):
        raise GradeError("policy confidence contract is invalid")
    thresholds = detector["thresholds"]
    _exact_object(thresholds, THRESHOLD_FLOORS, "policy thresholds")
    for name, floor in THRESHOLD_FLOORS.items():
        value = thresholds[name]
        if type(value) not in (int, float) or not math.isfinite(value) or value < floor:
            raise GradeError(f"policy threshold {name} is below trusted v1 floor")
    lower_bounds = detector["lower_bound_thresholds"]
    _exact_object(lower_bounds, LOWER_BOUND_FLOORS, "policy lower bound thresholds")
    for name, floor in LOWER_BOUND_FLOORS.items():
        value = lower_bounds[name]
        if type(value) not in (int, float) or not math.isfinite(value) or value < floor:
            raise GradeError(f"policy lower bound {name} is below trusted v1 floor")

    for field in (
        "artifact_roles", "required_command_ids", "forbidden_path_rules",
        "external_state_names",
    ):
        _string_list(policy[field], f"policy {field}")
    if set(policy["external_state_names"]) != {
        "adoption", "human_review", "marketplace_publication",
    }:
        raise GradeError("policy external states are invalid")
    if not isinstance(policy["limits"], dict) or not policy["limits"]:
        raise GradeError("policy limits are invalid")
    return _freeze(policy)


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def _path(value, label):
    if (not isinstance(value, str) or not value or "\\" in value or value.startswith("/") or
            any(part in ("", ".", "..") for part in value.split("/"))):
        raise GradeError(f"{label} is not a normalized repository-relative path")
    return value


def _count(value, name):
    if type(value) is not int or value < 0:
        raise GradeError(f"detector {name} must be a non-negative integer")
    return value


def _validate_detector(value, policy, subject):
    if not isinstance(value, dict) or tuple(sorted(value)) != LANGUAGES:
        raise GradeError("evidence detector languages are invalid")
    fields = {
        "subject_commit", "expected_rows", "attempted", "provider_completed", "decided",
        "tp", "fp", "fn", "tn",
    }
    for language in LANGUAGES:
        counts = value[language]
        _exact_object(counts, fields, f"detector {language}")
        if counts["subject_commit"] != subject["commit"]:
            raise GradeError(f"detector {language} has stale subject commit")
        numbers = {name: _count(counts[name], name) for name in fields - {"subject_commit"}}
        if numbers["attempted"] != numbers["expected_rows"]:
            raise GradeError(f"detector {language} has dropped rows")
        if not 0 <= numbers["decided"] <= numbers["provider_completed"] <= numbers["attempted"]:
            raise GradeError(f"detector {language} coverage counts are inconsistent")
        if sum(numbers[name] for name in ("tp", "fp", "fn", "tn")) != numbers["decided"]:
            raise GradeError(f"detector {language} confusion counts are inconsistent")
        if numbers["tp"] + numbers["fn"] < policy["detector"]["minimum_positive"]:
            raise GradeError(f"detector {language} has too few positive rows")
        if numbers["fp"] + numbers["tn"] < policy["detector"]["minimum_negative"]:
            raise GradeError(f"detector {language} has too few negative rows")


def _validate_peers(peers, subject):
    if not isinstance(peers, list) or not peers:
        raise GradeError("peer applicability is missing")
    seen = set()
    for peer in peers:
        _exact_object(peer, {"id", "applicability", "results"}, "peer")
        identifier = peer["id"]
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise GradeError("peer identity is invalid")
        seen.add(identifier)
        applicability = peer["applicability"]
        if not isinstance(applicability, dict) or tuple(sorted(applicability)) != LANGUAGES:
            raise GradeError("peer applicability languages are incomplete")
        if any(value not in ("applicable", "not-applicable") for value in applicability.values()):
            raise GradeError("peer applicability is invalid")
        results = peer["results"]
        if not isinstance(results, list):
            raise GradeError("peer results are invalid")
        by_language = {}
        for result in results:
            _exact_object(result, {"language", "subject_commit", "id_set_sha256"}, "peer result")
            language = result["language"]
            if language not in LANGUAGES or language in by_language:
                raise GradeError("peer results are invalid")
            if result["subject_commit"] != subject["commit"]:
                raise GradeError("peer result has stale subject commit")
            _hex(result["id_set_sha256"], "peer ID-set SHA-256", (64,))
            by_language[language] = result
        expected = {language for language, state in applicability.items() if state == "applicable"}
        if set(by_language) != expected:
            raise GradeError("peer results do not match applicability")
    if "direct-baseline" not in seen:
        raise GradeError("peer applicability is missing direct baseline")


def load_evidence(payload, policy):
    """Validate and freeze a manifest containing observations, never verdicts."""
    evidence = _load(payload)
    if not isinstance(evidence, dict):
        raise GradeError("evidence must be an object")
    keys = tuple(_walk_keys(evidence))
    if "evidence_head" in keys:
        raise GradeError("manifest cannot contain its runtime evidence_head")
    if any("threshold" in key.lower() for key in keys):
        raise GradeError("evidence cannot contain a threshold override")
    asserted = {"grade", "pass", "passed", "success"} & set(keys)
    if asserted:
        raise GradeError(f"evidence contains self-asserted field: {sorted(asserted)[0]}")
    _exact_object(evidence, EVIDENCE_FIELDS, "evidence")
    if (type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1 or
            evidence["kind"] != "evergreen-a-grade-evidence"):
        raise GradeError("evidence identity is invalid")
    if not isinstance(evidence["evaluated_release"], str) or not SEMVER.fullmatch(evidence["evaluated_release"]):
        raise GradeError("evaluated release is invalid")
    subject = _identity(evidence["subject"], "subject")
    policy_identity = evidence["policy"]
    _exact_object(policy_identity, {"id", "sha256"}, "evidence policy")
    if policy_identity["id"] != policy["policy_id"]:
        raise GradeError("evidence policy ID is invalid")
    _hex(policy_identity["sha256"], "policy SHA-256", (64,))
    if _string_list(evidence["required_categories"], "evidence categories") != CATEGORIES:
        raise GradeError("evidence categories are invalid")
    if _string_list(evidence["required_languages"], "evidence languages") != LANGUAGES:
        raise GradeError("evidence languages are invalid")
    _validate_detector(evidence["detector"], policy, subject)
    _validate_peers(evidence["peers"], subject)

    changed_paths = _string_list(evidence["changed_paths"], "changed paths")
    if changed_paths != tuple(sorted(changed_paths)):
        raise GradeError("changed paths must be sorted")
    release_root = f"eval/grade/public/{evidence['evaluated_release']}"
    allowed_changed_paths = {
        f"{release_root}/evidence.json",
        f"{release_root}/policy.json",
        f"{release_root}/report.md",
    }
    for path in changed_paths:
        _path(path, "changed path")
        if path not in allowed_changed_paths:
            raise GradeError(f"changed path is not a canonical release evidence path: {path}")

    executables = evidence["subject_executables"]
    if not isinstance(executables, list) or not executables:
        raise GradeError("subject executables are invalid")
    seen_paths = set()
    for executable in executables:
        _exact_object(
            executable, {"path", "subject_sha256", "evidence_sha256"}, "subject executable"
        )
        path = _path(executable["path"], "subject executable path")
        if path in seen_paths:
            raise GradeError("subject executable paths are duplicated")
        seen_paths.add(path)
        subject_hash = _hex(executable["subject_sha256"], "subject executable SHA-256", (64,))
        evidence_hash = _hex(executable["evidence_sha256"], "evidence executable SHA-256", (64,))
        if subject_hash != evidence_hash:
            raise GradeError(f"subject executable changed: {path}")

    external = evidence["external_states"]
    if not isinstance(external, dict) or set(external) != set(policy["external_state_names"]):
        raise GradeError("external states are invalid")
    if any(state not in EXTERNAL_STATES for state in external.values()):
        raise GradeError("external state is invalid")
    return _freeze(evidence)


def recompute_metrics(counts, prevalence):
    """Derive raw and fixed-prevalence metrics from confusion counts."""
    attempted = counts["attempted"]
    completed = counts["provider_completed"]
    decided = counts["decided"]
    tp, fp, fn, tn = (counts[name] for name in ("tp", "fp", "fn", "tn"))
    if not attempted or not completed or not tp + fn or not tn + fp:
        raise GradeError("detector counts cannot produce metrics")
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn)
    specificity = tn / (tn + fp)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    matrix = {
        "tp": prevalence * recall,
        "fp": (1 - prevalence) * (1 - specificity),
        "fn": prevalence * (1 - recall),
        "tn": (1 - prevalence) * specificity,
    }
    adjusted_positive = matrix["tp"] + matrix["fp"]
    adjusted_precision = matrix["tp"] / adjusted_positive if adjusted_positive else 0.0
    adjusted_recall = recall
    adjusted_f1 = (
        2 * adjusted_precision * adjusted_recall / (adjusted_precision + adjusted_recall)
        if adjusted_precision + adjusted_recall else 0.0
    )
    return {
        "provider_completion": completed / attempted,
        "semantic_coverage": decided / completed,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "specificity": specificity,
        "prevalence_matrix": matrix,
        "prevalence_precision": adjusted_precision,
        "prevalence_recall": adjusted_recall,
        "prevalence_f1": adjusted_f1,
        "prevalence_specificity": specificity,
    }


def _predicate_results(policy, supplied):
    if not isinstance(supplied, dict) or set(supplied) != set(CATEGORIES):
        raise GradeError("trusted predicate categories are invalid")
    result = {}
    for category in CATEGORIES:
        gates = supplied[category]
        expected = set(policy["category_gates"][category])
        if not isinstance(gates, dict) or set(gates) != expected:
            raise GradeError(f"trusted predicates for {category} are incomplete")
        if any(type(value) is not bool for value in gates.values()):
            raise GradeError(f"trusted predicates for {category} are invalid")
        result[category] = gates
    return result


def evaluate(policy, evidence, evidence_head, trusted_predicates, trusted_repository=None):
    """Derive a receipt from validated evidence and trusted runtime observations."""
    head = _identity(evidence_head, "evidence head")
    subject = _plain(evidence["subject"])
    if head["commit"] == subject["commit"]:
        raise GradeError("evidence head must be later than subject")
    repository_fields = {
        "subject_ancestor_of_evidence_head", "evidence_head_is_exact",
    }
    if (not isinstance(trusted_repository, dict) or
            set(trusted_repository) != repository_fields or
            any(type(value) is not bool for value in trusted_repository.values())):
        raise GradeError("trusted repository observation is invalid")
    predicates = _predicate_results(policy, trusted_predicates)
    prevalence = policy["detector"]["prevalence"]
    thresholds = policy["detector"]["thresholds"]
    all_metrics = {
        language: recompute_metrics(evidence["detector"][language], prevalence)
        for language in LANGUAGES
    }
    detector_reasons = ["detector:repository-clustered-bounds-missing"]
    adjusted_names = {
        "precision": "prevalence_precision",
        "recall": "prevalence_recall",
        "f1": "prevalence_f1",
        "specificity": "prevalence_specificity",
    }
    for language, metrics in all_metrics.items():
        for name, threshold in thresholds.items():
            observed = metrics[adjusted_names.get(name, name)]
            if observed < threshold:
                detector_reasons.append(f"detector:{language}:{name}")

    categories = []
    for category in CATEGORIES:
        reasons = [
            f"predicate:{gate}" for gate, passed in predicates[category].items() if not passed
        ]
        if category == "detector_quality":
            reasons.extend(detector_reasons)
        if category == "reproducibility_ci":
            if not trusted_repository["subject_ancestor_of_evidence_head"]:
                reasons.append("repository:subject-not-ancestor")
            if not trusted_repository["evidence_head_is_exact"]:
                reasons.append("repository:evidence-head-not-exact")
        reasons = sorted(set(reasons))
        categories.append({
            "id": category,
            "status": "earned" if not reasons else "not-earned",
            "reasons": reasons,
        })
    earned = all(category["status"] == "earned" for category in categories)
    return {
        "schema_version": 1,
        "kind": "evergreen-a-grade-verification",
        "status": "earned" if earned else "not-earned",
        "grade": "A" if earned else None,
        "subject": subject,
        "evidence_head": head,
        "repository_observation": dict(trusted_repository),
        "policy": {
            "id": policy["policy_id"],
            "sha256": evidence["policy"]["sha256"],
            "thresholds": _plain(thresholds),
        },
        "categories": categories,
        "detector_metrics": all_metrics,
        "external_states": _plain(evidence["external_states"]),
    }


def canonical_receipt(receipt):
    """Serialize a derived receipt without timestamps or platform variance."""
    try:
        return (json.dumps(
            receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ) + "\n").encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise GradeError("receipt is not finite JSON") from error
