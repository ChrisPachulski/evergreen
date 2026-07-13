"""Pure, versioned interpretation of collected benchmark trial stages."""

VERDICTS = {"consistent", "inconsistent"}


def _value(stages, name):
    result = stages.get(name)
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    value = result.get("value")
    return value if isinstance(value, dict) else None


def _prongs(stages):
    records = stages.get("prongs_escalated") or stages.get("prongs")
    if not isinstance(records, list) or len(records) != 3:
        return None
    values = []
    for record in records:
        if not isinstance(record, dict) or record.get("status") != "ok":
            return None
        value = record.get("value")
        if not isinstance(value, dict) or value.get("verdict") not in VERDICTS:
            return None
        values.append(value)
    return values


def _validated_v1(stages):
    snap = _value(stages, "snap")
    challenge = _value(stages, "challenge")
    blindspot = _value(stages, "blindspot")
    prongs = _prongs(stages)
    if (not snap or snap.get("verdict") not in VERDICTS or not challenge or
            type(challenge.get("cracks")) is not bool or not blindspot or
            "missed_angle" not in blindspot or prongs is None):
        return None
    missed = blindspot["missed_angle"]
    if missed is not None and (not isinstance(missed, str) or not missed.strip()):
        return None
    return snap, challenge, prongs, blindspot


def _abstain(stages, reason):
    return {
        "final_status": "abstain", "final_verdict": None, "verdict": None,
        "category": None, "why": reason, "contested": False, "stages": stages,
    }


def needs_synthesis_v1(stages):
    values = _validated_v1(stages)
    if values is None:
        return True
    snap, _challenge, prongs, blindspot = values
    votes = [snap["verdict"], *(prong["verdict"] for prong in prongs)]
    return bool(blindspot["missed_angle"]) or len(set(votes)) != 1


def resolve_v1(stages):
    """Reproduce the cb24647 trial decision policy without invoking a model."""
    values = _validated_v1(stages)
    if values is None:
        return _abstain(stages, "trial record is incomplete")
    snap, challenge, prongs, blindspot = values
    missed = bool(blindspot["missed_angle"])
    votes = [snap["verdict"], *(prong["verdict"] for prong in prongs)]
    source = snap
    if missed or len(set(votes)) != 1:
        source = _value(stages, "synthesis")
        if not source or source.get("verdict") not in VERDICTS:
            return _abstain(stages, "synthesis response is missing required fields")
    verdict = source["verdict"]
    return {
        "final_status": "complete", "final_verdict": verdict, "verdict": verdict,
        "category": source.get("category"), "why": source.get("why"),
        "contested": challenge["cracks"] or "prongs_escalated" in stages or missed,
        "stages": stages,
    }


def resolve(stages, resolver_id):
    if resolver_id == "v1":
        return resolve_v1(stages)
    raise ValueError("resolver must be v1")
