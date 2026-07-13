"""Statistics and publication gates for independently submitted human labels."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Estimate:
    point: float
    lower: float
    upper: float


@dataclass(frozen=True)
class AuditResult:
    language: str
    stratum: str
    label_error: bool
    inclusion_probability: float


@dataclass(frozen=True)
class GateInputs:
    overall_kappa: float
    overall_kappa_lower: float
    language_kappa: dict[str, float | None]
    overall_error_upper: float
    language_error_upper: dict[str, float]
    max_census_error: float
    discarded_usable_rate: float
    unresolved_count: int
    missing_discarded_languages: tuple[str, ...]
    census_complete: bool


@dataclass(frozen=True)
class GateResult:
    status: str
    qualification: str | None
    reasons: tuple[str, ...]


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("kappa requires equal non-empty sequences")
    categories = ("consistent", "inconsistent")
    if any(value not in categories for value in (*a, *b)):
        raise ValueError("kappa accepts binary decisive labels only")
    observed = sum(x == y for x, y in zip(a, b)) / len(a)
    expected = sum((a.count(category) / len(a)) * (b.count(category) / len(b))
                   for category in categories)
    if expected == 1.0:
        raise ValueError("kappa is undefined for a single class")
    return (observed - expected) / (1 - expected)


def bootstrap_kappa_ci(a: Sequence[str], b: Sequence[str], strata: Sequence[str], *,
                       replicates: int = 10_000, seed: int = 20260713) -> tuple[float, float]:
    if not (len(a) == len(b) == len(strata)):
        raise ValueError("bootstrap inputs must have equal length")
    groups = {name: [i for i, value in enumerate(strata) if value == name]
              for name in sorted(set(strata))}
    rng, values = random.Random(seed), []
    for _ in range(replicates):
        indices = [index for group in groups.values()
                   for index in rng.choices(group, k=len(group))]
        try:
            values.append(cohen_kappa([a[i] for i in indices], [b[i] for i in indices]))
        except ValueError:
            continue
    if not values:
        raise ValueError("bootstrap kappa is undefined")
    values.sort()
    return values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]


def wilson_interval(errors: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0 or not 0 <= errors <= n:
        raise ValueError("Wilson interval requires 0 <= errors <= n")
    proportion = errors / n
    denominator = 1 + z * z / n
    centre = (proportion + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _weighted_point(rows: Sequence[AuditResult]) -> float:
    weights = [1 / row.inclusion_probability for row in rows]
    return sum(weight for row, weight in zip(rows, weights) if row.label_error) / sum(weights)


def weighted_error(rows: Sequence[AuditResult], *, replicates: int = 10_000,
                   seed: int = 20260713) -> Estimate:
    if not rows or any(not 0 < row.inclusion_probability <= 1 for row in rows):
        raise ValueError("weighted error requires rows with valid inclusion probabilities")
    point = _weighted_point(rows)
    groups = {(row.language, row.stratum): [] for row in rows}
    for row in rows:
        groups[row.language, row.stratum].append(row)
    rng, estimates = random.Random(seed), []
    for _ in range(replicates):
        sample = [row for group in groups.values()
                  for row in rng.choices(group, k=len(group))]
        estimates.append(_weighted_point(sample))
    estimates.sort()
    return Estimate(point, estimates[int(0.025 * (len(estimates) - 1))],
                    estimates[int(0.975 * (len(estimates) - 1))])


def evaluate_gate(inputs: GateInputs) -> GateResult:
    if inputs.unresolved_count:
        raise ValueError("unresolved human judgments prevent gate evaluation")
    if inputs.missing_discarded_languages:
        return GateResult("unverified", None,
                          tuple(f"missing historical source pool: {language}"
                                for language in inputs.missing_discarded_languages))
    reasons = []
    if inputs.overall_kappa < 0.70:
        reasons.append("overall kappa")
    if inputs.overall_kappa_lower < 0.60:
        reasons.append("overall kappa lower bound")
    reasons += [f"{language} kappa" for language, value in inputs.language_kappa.items()
                if value is None or value < 0.60]
    if inputs.overall_error_upper > 0.05:
        reasons.append("overall label-error upper bound")
    reasons += [f"{language} label-error upper bound"
                for language, value in inputs.language_error_upper.items() if value > 0.10]
    if inputs.max_census_error > 0.05:
        reasons.append("census stratum error")
    if inputs.discarded_usable_rate > 0.05:
        reasons.append("discarded usable rate")
    if reasons:
        return GateResult("escalate", None, tuple(reasons))
    return GateResult("pass", "human-validated" if inputs.census_complete else "human-audited", ())


def render_audit_report(inputs: GateInputs, result: GateResult) -> str:
    lines = ["# Evergreen human-label audit", "", f"Status: **{result.status}**.", "",
             "Human status is self-attested and not machine-verifiable.", "",
             f"Overall Cohen's kappa: `{inputs.overall_kappa:.3f}`",
             f"Overall label-error upper bound: `{inputs.overall_error_upper:.3f}`"]
    if result.reasons:
        lines += ["", "Reasons:"] + [f"- {reason}" for reason in result.reasons]
    return "\n".join(lines) + "\n"
