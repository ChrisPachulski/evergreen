"""Benchmark confusion matrices, prevalence resampling, and shared reporting."""

import random
import statistics

CORE_CATEGORIES = {None, "direct-mismatch", "over-promise"}
VERDICTS = {"consistent", "inconsistent"}


def score(rows):
    """Return completed-row metrics, abstention coverage, and the under-promise tally."""
    languages = {row.get("language", "unknown") for row in rows}
    if len(languages) > 1:
        raise ValueError("score accepts one language at a time")

    def completed(row):
        status = row.get("final_status")
        verdict = row.get("final_verdict") if status is not None else row.get("verdict")
        return (status in (None, "complete")) and verdict in VERDICTS

    def verdict(row):
        return row.get("final_verdict") if row.get("final_status") is not None else row.get("verdict")

    attempted = len(rows)
    completed_rows = [r for r in rows if completed(r)]
    core = [r for r in completed_rows if r["category"] in CORE_CATEGORIES]
    under = [r for r in rows if r["category"] == "under-promise"]
    under_completed = [r for r in under if completed(r)]
    tp = sum(r["label"] == "inconsistent" and verdict(r) == "inconsistent" for r in core)
    fp = sum(r["label"] == "consistent" and verdict(r) == "inconsistent" for r in core)
    fn = sum(r["label"] == "inconsistent" and verdict(r) == "consistent" for r in core)
    tn = sum(r["label"] == "consistent" and verdict(r) == "consistent" for r in core)
    metrics_available = any(r["label"] == "inconsistent" for r in core) and \
        any(r["label"] == "consistent" for r in core)
    if metrics_available:
        n = len(core)
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 1.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        specificity = tn / (tn + fp)
        accuracy = (tp + tn) / n
        flag_rate = (tp + fp) / n
    else:
        precision = recall = f1 = specificity = accuracy = flag_rate = None
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "metrics_available": metrics_available,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "specificity": specificity,
        "accuracy": accuracy,
        "flag_rate": flag_rate,
        "attempted": attempted,
        "completed": len(completed_rows),
        "abstained": attempted - len(completed_rows),
        "completion_rate": len(completed_rows) / attempted if attempted else 0.0,
        "under_flagged": sum(verdict(r) == "inconsistent" for r in under_completed),
        "under_total": len(under),
        "under_attempted": len(under),
        "under_completed": len(under_completed),
        "under_abstained": len(under) - len(under_completed),
        "under_completion_rate": len(under_completed) / len(under) if under else 0.0,
    }


def split_metrics(rows, pos_frac, resamples=1000, seed=0):
    """Median metrics at a fixed prevalence: keep every inconsistent core pair, resample the
    consistent class to the target ratio (CASCADE's protocol, arXiv:2604.19400)."""
    core = [r for r in rows if r["category"] in CORE_CATEGORIES and
            ((r.get("final_status") is None and r.get("verdict") in VERDICTS) or
             (r.get("final_status") == "complete" and r.get("final_verdict") in VERDICTS))]
    pos = [r for r in core if r["label"] == "inconsistent"]
    neg = [r for r in core if r["label"] == "consistent"]
    if not pos or not neg:
        return {"metrics_available": False, "n_pos": len(pos), "n_neg": len(neg),
                "resamples": 0, "with_replacement": False,
                **dict.fromkeys(("precision", "recall", "f1", "specificity", "flag_rate"))}
    n_neg = round(len(pos) * (1 - pos_frac) / pos_frac)
    with_repl = n_neg > len(neg)  # tiny sets can't seat 9x consistent pairs; bootstrap instead
    rng = random.Random(seed)
    samples = [score(pos + (rng.choices(neg, k=n_neg) if with_repl
                            else rng.sample(neg, n_neg))) for _ in range(resamples)]
    med = lambda k: statistics.median(s[k] for s in samples)
    return {"metrics_available": True, "n_pos": len(pos), "n_neg": n_neg,
            "resamples": resamples, "with_replacement": with_repl,
            **{k: med(k) for k in ("precision", "recall", "f1", "specificity", "flag_rate")}}


def _report_language(rows, label):
    m = score(rows)
    n = m["tp"] + m["fp"] + m["fn"] + m["tn"]
    print(f"\ncompletion: {m['completed']}/{m['attempted']} completed, {m['abstained']} abstained"
          f" ({m['completion_rate']:.1%})")
    print(f"\ncore set (consistent + direct-mismatch + over-promise), n={n}{label}")
    if m["metrics_available"]:
        nat = split_metrics(rows, 0.10)
        bal = split_metrics(rows, 0.50)
        print(f"  NATURAL 10/90 split (headline; {nat['n_pos']} inconsistent + {nat['n_neg']} consistent"
              f"{', consistent bootstrapped WITH replacement' if nat['with_replacement'] else ''},"
              f" medians over {nat['resamples']} resamples):")
        print(f"    precision {nat['precision']:.2f}  recall {nat['recall']:.2f}  F1 {nat['f1']:.2f}"
              f"  specificity {nat['specificity']:.2f}  flag-rate {nat['flag_rate']:.2f}")
        print(f"  balanced 50/50 split ({bal['n_pos']}+{bal['n_neg']}, medians over {bal['resamples']} resamples):")
        print(f"    precision {bal['precision']:.2f}  recall {bal['recall']:.2f}  F1 {bal['f1']:.2f}"
              f"  specificity {bal['specificity']:.2f}  flag-rate {bal['flag_rate']:.2f}")
        print(f"  raw full set: precision {m['precision']:.2f}  recall {m['recall']:.2f}"
              f"  accuracy {m['accuracy']:.2f}  flag-rate {m['flag_rate']:.2f}"
              f"  |  TP {m['tp']}  FP {m['fp']}  FN {m['fn']}  TN {m['tn']}")
    else:
        print("  metrics unavailable: completed core rows must include both label classes")
        print(f"  raw counts: TP {m['tp']}  FP {m['fp']}  FN {m['fn']}  TN {m['tn']}")
    print(f"under-promise (informational by design, not scored as drift): "
          f"flagged {m['under_flagged']}/{m['under_completed']} completed; completion "
          f"{m['under_completed']}/{m['under_attempted']}, {m['under_abstained']} abstained "
          f"({m['under_completion_rate']:.1%})")
    print("baseline regime: the peer is DocPrism (arXiv:2511.00215) — 0.62 precision @ 15% flag"
          " rate, multi-language, no fine-tuning. Fine-tuned single-language SOTA (F1 0.88-0.94)"
          " is a different regime and out of scope.")


def report(rows, label=""):
    languages = sorted({row.get("language", "unknown") for row in rows})
    for language in languages or ["unknown"]:
        language_rows = [row for row in rows if row.get("language", "unknown") == language]
        _report_language(language_rows, f"{label}, language={language}")

def rows_from_transcript(transcript):
    rows = []
    for item in transcript:
        got = item.get("got") or {}
        if got.get("final_status") == "complete" and got.get("final_verdict") in VERDICTS:
            status, verdict = "complete", got["final_verdict"]
        elif "final_status" not in got and got.get("verdict") in VERDICTS:
            status, verdict = "complete", got["verdict"]  # legacy completed artifact
        else:
            status, verdict = "abstain", None
        rows.append({"language": item.get("language", "unknown"),
                     "label": item["label"], "category": item["category"],
                     "final_status": status, "final_verdict": verdict})
    return rows
