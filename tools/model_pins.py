"""Checks evergreen's pinned Claude model IDs and Claude Code CLI version against a lineup file; `apply` moves stale pins.
The lineup is any file with one `<!-- latest-models: ID ... claude-code@X.Y.Z -->` line. Evergreen never reads it at runtime.
Usage: python3 tools/model_pins.py check|apply --lineup FILE [--root DIR]. Exit 0 current, 1 behind, 2 unusable.
Not pins: frozen benchmark records, bench runner defaults, and the judge, which follows the host's Codex config."""

############### Imports and constants

import argparse
import re
import sys
from pathlib import Path

MODEL_ID = r"claude-[a-z]+-[0-9][a-z0-9-]*"
CLI_ID = r"[0-9]+\.[0-9]+\.[0-9]+"
MARKER_RE = re.compile(r"<!--\s*latest-models:\s*(.*?)\s*-->")
CLI_TOKEN_RE = re.compile(rf"claude-code@({CLI_ID})")
DATE_PART_RE = re.compile(r"\d{8}")
PINS = (                                                                    # (path, pattern, role, kind); a model pin occurs once, a cli pin may repeat
    ("action.yml", rf'(?m)^    default: "({MODEL_ID})"$', "Action default model", "model"),
    ("ci/evergreen-pr.sh", rf"EVERGREEN_MODEL:-({MODEL_ID})\}}", "CI fallback model", "model"),
    ("eval/README.md", rf"EVAL_MODEL=({MODEL_ID}) bash eval/run\.sh", "eval run example", "model"),
    ("eval/seed/README.md", rf"EVAL_MODEL=({MODEL_ID}) bash eval/seed/run\.sh", "seed eval run example", "model"),
    ("action.yml", rf"@anthropic-ai/claude-code@({CLI_ID})\b", "Action Claude Code CLI", "cli"),
    ("tests/action.sh", rf"@anthropic-ai/claude-code@({CLI_ID})\b", "test CLI pin", "cli"),
    ("tests/action.sh", rf"({CLI_ID}) \(Claude Code\)", "test CLI stub identity", "cli"),
)

############### Functions

def lineup_tokens(text: str) -> list[str]:
    """Tokens on the file's single latest-models line; empty when there is not exactly one line."""
    markers = MARKER_RE.findall(text)
    return markers[0].split() if len(markers) == 1 else []


def family(model: str) -> str:
    """Model family, such as opus or haiku."""
    return model.split("-")[1]


def version(pin: str, kind: str) -> tuple[int, ...]:
    """Comparable version; a model's dated snapshot suffix is not part of it."""
    parts = pin.split(".") if kind == "cli" else [p for p in pin.split("-")[2:] if p.isdigit() and not DATE_PART_RE.fullmatch(p)]
    return tuple(int(p) for p in parts)


def latest_for(current: str, kind: str, tokens: list[str]) -> str:
    """Newest lineup value for this pin: same model family, or the claude-code token; empty when absent."""
    if kind == "cli":
        found = [m.group(1) for m in map(CLI_TOKEN_RE.fullmatch, tokens) if m]
        return found[0] if len(found) == 1 else ""
    candidates = [t for t in tokens if re.fullmatch(MODEL_ID, t) and family(t) == family(current)]
    return max(candidates, key=lambda t: version(t, "model")) if candidates else ""


def pin_state(root: Path, pin: tuple[str, str, str, str], tokens: list[str]) -> dict[str, str]:
    """Where one pin stands against the lineup: CURRENT, BEHIND, AHEAD, UNKNOWN or MISSING."""
    path, pattern, role, kind = pin
    file = root / path
    matches = re.findall(pattern, file.read_text(encoding="utf-8")) if file.is_file() else []
    usable = len(set(matches)) == 1 and (kind == "cli" or len(matches) == 1)
    current = matches[0] if usable else ""
    latest = latest_for(current, kind, tokens) if current else ""
    status = (
        "MISSING" if not current
        else "UNKNOWN" if not latest
        else "BEHIND" if version(latest, kind) > version(current, kind)
        else "AHEAD" if version(latest, kind) < version(current, kind)
        else "CURRENT"
    )
    return {"path": path, "pattern": pattern, "role": role, "kind": kind, "current": current, "latest": latest, "status": status}


def report_line(state: dict[str, str]) -> str:
    """One line per pin, status first so the output greps."""
    detail = {
        "MISSING": "pin not found, found more than once, or found with differing values; update PINS in tools/model_pins.py",
        "UNKNOWN": f"{state['current']} has no {state['kind']} match in the lineup",
        "BEHIND": f"{state['current']} -> {state['latest']}",
        "AHEAD": f"{state['current']} is newer than the lineup's {state['latest']}; left as is",
        "CURRENT": state["current"],
    }[state["status"]]
    return f"{state['status']} {state['path']} ({state['role']}): {detail}"


def apply_pin(root: Path, state: dict[str, str]) -> str:
    """Rewrite one BEHIND pin, every occurrence, in place; returns what changed, empty when nothing did."""
    if state["status"] != "BEHIND":
        return ""
    file = root / state["path"]
    text = file.read_text(encoding="utf-8")
    moved = re.sub(state["pattern"], lambda m: m.group(0).replace(m.group(1), state["latest"]), text)
    file.write_text(moved, encoding="utf-8")
    return f"UPDATED {state['path']} ({state['role']}): {state['current']} -> {state['latest']}"


def exit_code(states: list[dict[str, str]], mode: str) -> int:
    """2 when a pin or the lineup is unusable, 1 when check finds a stale pin, else 0."""
    statuses = {s["status"] for s in states}
    return 2 if statuses & {"MISSING", "UNKNOWN"} else 1 if mode == "check" and "BEHIND" in statuses else 0

############### Run

PARSER = argparse.ArgumentParser(description="Check or apply evergreen's Claude model and CLI pins against a lineup file.")
PARSER.add_argument("mode", choices=("check", "apply"))
PARSER.add_argument("--lineup", required=True, type=Path)
PARSER.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
ARGS = PARSER.parse_args()
TOKENS = lineup_tokens(ARGS.lineup.read_text(encoding="utf-8")) if ARGS.lineup.is_file() else []
if not TOKENS:
    print(f"FAILED: {ARGS.lineup} has no single latest-models line")
    sys.exit(2)
STATES = [pin_state(ARGS.root, pin, TOKENS) for pin in PINS]
CHANGES = [apply_pin(ARGS.root, s) for s in STATES] if ARGS.mode == "apply" else []
print("\n".join([report_line(s) for s in STATES] + [c for c in CHANGES if c]))
sys.exit(exit_code(STATES, ARGS.mode))
