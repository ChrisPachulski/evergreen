"""Which ladder rungs each intensity may emit, and how to prove a run obeyed it.

Intensity used to be an instruction and nothing more: the session preamble asked light-mode
runs to defer the semantic rung, and no artifact anywhere recorded whether they had. A mode
that cannot be checked is a suggestion, and this project does not get to ship suggestions.

Findings now name the rung that proved them, so the claim is finally falsifiable. A light run
that emits a `prose` finding contradicts its own declared mode, and that contradiction is
visible in the output text without re-running anything.

The boundary is honest and worth stating: nothing here stops a model mid-turn. The reflex runs
inside someone else's agent session and evergreen's hooks deliberately never read output. What
this buys is a machine-checkable definition, an output format that exposes the violation, and
a checker anyone can run over a transcript afterward. That is enforcement after the fact, not
prevention -- and it is the strongest honest claim available for a behavior a prompt requests.
"""

from __future__ import annotations

import re

# Ladder order. `prose` is rung 4 -- the only rung resting on judgment rather than a
# mechanical check, and therefore the only one an intensity has any reason to withhold.
LADDER = ("path", "contract", "snippet", "prose")

MODE_RUNGS: dict[str, frozenset[str]] = {
    "off": frozenset(),
    "light": frozenset(LADDER[:3]),
    "strict": frozenset(LADDER),
}

# `[high] in_docs_not_code contract  README.md:42 - ...`
FINDING_RE = re.compile(
    r"^\s*\[(?P<severity>high|med|low)\]\s+(?P<category>[a-z_]+)\s+(?P<rung>[a-z]+)\b"
)
# `evergreen [light]: you renamed ...`
HEADER_RE = re.compile(r"^\s*evergreen\s*\[(?P<mode>off|light|strict)\]\s*:")


def declared_mode(text: str) -> str | None:
    """The intensity a run declared in its own output, or None when it declared none."""
    for line in text.splitlines():
        found = HEADER_RE.match(line)
        if found:
            return found.group("mode")
    return None


def emitted_rungs(text: str) -> list[tuple[int, str]]:
    """Every (line number, rung) a run's findings claim, in order."""
    return [
        (number, found.group("rung"))
        for number, line in enumerate(text.splitlines(), start=1)
        for found in [FINDING_RE.match(line)]
        if found
    ]


def violations(text: str, mode: str | None = None) -> list[str]:
    """Every way a run's output contradicts the intensity it ran under.

    An undeclared mode is itself a violation when none is supplied: a run that never says
    which intensity it used cannot be held to one, and unfalsifiable is the state this
    module exists to remove.
    """
    resolved = mode or declared_mode(text)
    if resolved is None:
        return ["no intensity declared: output must open with `evergreen [<mode>]:`"]
    if resolved not in MODE_RUNGS:
        return [f"unknown intensity {resolved!r}: expected off, light, or strict"]
    allowed = MODE_RUNGS[resolved]
    found = []
    for number, rung in emitted_rungs(text):
        if rung not in LADDER:
            found.append(f"line {number}: unknown rung {rung!r}; expected one of {', '.join(LADDER)}")
        elif rung not in allowed:
            found.append(
                f"line {number}: intensity {resolved} emitted a {rung} finding; "
                f"{resolved} may only emit {', '.join(sorted(allowed)) or 'nothing'}"
            )
    return found
