"""Checks evergreen's pinned Claude model IDs against a lineup file; `apply` moves the stale ones to it.
The lineup is any file with one `<!-- latest-models: ID ID ... -->` line. Evergreen never reads it at runtime.
Usage: python3 tools/model_pins.py check|apply --lineup FILE [--root DIR]. Exit 0 current, 1 behind, 2 unusable.
Not pins: frozen benchmark records, bench runner defaults, and the judge, which follows the host's Codex config."""

############### Imports and constants

import argparse
import re
import sys
from pathlib import Path

MODEL_ID = r"claude-[a-z]+-[0-9][a-z0-9-]*"
MARKER_RE = re.compile(r"<!--\s*latest-models:\s*(.*?)\s*-->")
DATE_PART_RE = re.compile(r"\d{8}")
PINS = (
    ("action.yml", rf'(?m)^    default: "({MODEL_ID})"$', "Action default model"),
    ("ci/evergreen-pr.sh", rf"EVERGREEN_MODEL:-({MODEL_ID})\}}", "CI fallback model"),
    ("eval/README.md", rf"EVAL_MODEL=({MODEL_ID}) bash eval/run\.sh", "eval run example"),
    ("eval/seed/README.md", rf"EVAL_MODEL=({MODEL_ID}) bash eval/seed/run\.sh", "seed eval run example"),
)

############### Functions

def lineup_ids(text: str) -> list[str]:
    """Claude IDs on the file's single latest-models line; empty when there is not exactly one line."""
    markers = MARKER_RE.findall(text)
    return [m for m in markers[0].split() if re.fullmatch(MODEL_ID, m)] if len(markers) == 1 else []


def family(model: str) -> str:
    """Model family, such as opus or haiku."""
    return model.split("-")[1]


def version(model: str) -> tuple[int, ...]:
    """Numeric version parts; a dated snapshot suffix is not part of the version."""
    return tuple(int(p) for p in model.split("-")[2:] if p.isdigit() and not DATE_PART_RE.fullmatch(p))


def latest_for(model: str, lineup: list[str]) -> str:
    """Newest lineup ID in the pinned model's family, or empty when the lineup has none."""
    candidates = [m for m in lineup if family(m) == family(model)]
    return max(candidates, key=version) if candidates else ""


def pin_state(root: Path, pin: tuple[str, str, str], lineup: list[str]) -> dict[str, str]:
    """Where one pin stands against the lineup: CURRENT, BEHIND, AHEAD, UNKNOWN or MISSING."""
    path, pattern, role = pin
    file = root / path
    matches = re.findall(pattern, file.read_text(encoding="utf-8")) if file.is_file() else []
    current = matches[0] if len(matches) == 1 else ""
    latest = latest_for(current, lineup) if current else ""
    status = (
        "MISSING" if not current
        else "UNKNOWN" if not latest
        else "BEHIND" if version(latest) > version(current)
        else "AHEAD" if version(latest) < version(current)
        else "CURRENT"
    )
    return {"path": path, "pattern": pattern, "role": role, "current": current, "latest": latest, "status": status}


def report_line(state: dict[str, str]) -> str:
    """One line per pin, status first so the output greps."""
    detail = {
        "MISSING": "pin not found exactly once; update PINS in tools/model_pins.py",
        "UNKNOWN": f"{state['current']} has no family match in the lineup",
        "BEHIND": f"{state['current']} -> {state['latest']}",
        "AHEAD": f"{state['current']} is newer than the lineup's {state['latest']}; left as is",
        "CURRENT": state["current"],
    }[state["status"]]
    return f"{state['status']} {state['path']} ({state['role']}): {detail}"


def apply_pin(root: Path, state: dict[str, str]) -> str:
    """Rewrite one BEHIND pin in place; returns what changed, empty when nothing did."""
    if state["status"] != "BEHIND":
        return ""
    file = root / state["path"]
    text = file.read_text(encoding="utf-8")
    moved = re.sub(state["pattern"], lambda m: m.group(0).replace(m.group(1), state["latest"]), text, count=1)
    file.write_text(moved, encoding="utf-8")
    return f"UPDATED {state['path']}: {state['current']} -> {state['latest']}"


def exit_code(states: list[dict[str, str]], mode: str) -> int:
    """2 when a pin or the lineup is unusable, 1 when check finds a stale pin, else 0."""
    statuses = {s["status"] for s in states}
    return 2 if statuses & {"MISSING", "UNKNOWN"} else 1 if mode == "check" and "BEHIND" in statuses else 0

############### Run

PARSER = argparse.ArgumentParser(description="Check or apply evergreen's Claude model pins against a lineup file.")
PARSER.add_argument("mode", choices=("check", "apply"))
PARSER.add_argument("--lineup", required=True, type=Path)
PARSER.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
ARGS = PARSER.parse_args()
LINEUP = lineup_ids(ARGS.lineup.read_text(encoding="utf-8")) if ARGS.lineup.is_file() else []
if not LINEUP:
    print(f"FAILED: {ARGS.lineup} has no single latest-models line with Claude IDs")
    sys.exit(2)
STATES = [pin_state(ARGS.root, pin, LINEUP) for pin in PINS]
CHANGES = [apply_pin(ARGS.root, s) for s in STATES] if ARGS.mode == "apply" else []
print("\n".join([report_line(s) for s in STATES] + [c for c in CHANGES if c]))
sys.exit(exit_code(STATES, ARGS.mode))
