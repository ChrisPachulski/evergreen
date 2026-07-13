"""Pure, versioned interpretation of collected benchmark trial stages."""

VERDICTS = {"consistent", "inconsistent"}
V2_VERDICTS = VERDICTS | {"unverified"}
PROOFS = {"direct", "delegated", "requires-unseen-code"}
V2_PRONG_ROLES = ("defend", "prove-wrong", "evidence-auditor")
V2_CATEGORIES = {None, "direct-mismatch", "over-promise", "under-promise"}


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


def _proof_record(value, role=None):
    if not isinstance(value, dict):
        return None
    if (value.get("verdict") not in V2_VERDICTS or value.get("proof") not in PROOFS or
            "category" not in value or value.get("category") not in V2_CATEGORIES):
        return None
    if any(not isinstance(value.get(field), str) or not value[field].strip()
           for field in ("claim", "evidence")):
        return None
    if role is not None and value.get("role") != role:
        return None
    return value


def _validated_v2(stages):
    snap = _proof_record(_value(stages, "snap"))
    challenge = _value(stages, "challenge")
    blindspot = _value(stages, "blindspot")
    records = stages.get("prongs")
    if (snap is None or not challenge or type(challenge.get("cracks")) is not bool or
            not blindspot or "missed_angle" not in blindspot or
            not isinstance(records, list) or len(records) != len(V2_PRONG_ROLES)):
        return None
    missed = blindspot["missed_angle"]
    if missed is not None and (not isinstance(missed, str) or not missed.strip()):
        return None
    prongs = []
    for record, role in zip(records, V2_PRONG_ROLES):
        if not isinstance(record, dict) or record.get("status") != "ok":
            return None
        value = _proof_record(record.get("value"), role)
        if value is None:
            return None
        prongs.append(value)
    return snap, challenge, prongs, blindspot


def _abstain_v2(stages, reason):
    return {
        "final_status": "abstain", "semantic_status": "not-evaluated",
        "final_verdict": None, "verdict": None, "category": None,
        "why": reason, "contested": False, "stages": stages,
    }


def needs_synthesis_v2(stages):
    values = _validated_v2(stages)
    if values is None:
        return True
    snap, challenge, prongs, blindspot = values
    votes = [snap, *prongs]
    return (
        challenge["cracks"] or bool(blindspot["missed_angle"]) or
        len({vote["verdict"] for vote in votes}) != 1 or
        any(vote["proof"] != "direct" or vote["verdict"] == "unverified"
            for vote in votes)
    )


def resolve_v2(stages):
    """Resolve proof-bearing stages, preserving uncertainty as a semantic outcome."""
    values = _validated_v2(stages)
    if values is None:
        return _abstain_v2(stages, "trial record is incomplete")
    snap, challenge, _prongs, blindspot = values
    contested = needs_synthesis_v2(stages)
    source = snap
    if contested:
        source = _proof_record(_value(stages, "synthesis"))
        if source is None:
            return _abstain_v2(stages, "synthesis response is missing required fields")

    verdict = source["verdict"]
    direct_consistent = verdict == "consistent" and source["proof"] == "direct"
    direct_inconsistent = (
        verdict == "inconsistent" and source["proof"] == "direct" and
        source["category"] in {"direct-mismatch", "over-promise"}
    )
    decided = direct_consistent or direct_inconsistent
    final_verdict = verdict if decided else None
    return {
        "final_status": "complete",
        "semantic_status": "decided" if decided else "unverified",
        "final_verdict": final_verdict, "verdict": final_verdict,
        "category": source["category"] if direct_inconsistent else None,
        "why": source["evidence"],
        "proof": source["proof"], "claim": source["claim"],
        "evidence": source["evidence"],
        "contested": contested or challenge["cracks"] or bool(blindspot["missed_angle"]),
        "stages": stages,
    }


def resolve(stages, resolver_id):
    if resolver_id == "v1":
        return resolve_v1(stages)
    if resolver_id == "v2":
        return resolve_v2(stages)
    raise ValueError("resolver must be v1 or v2")
