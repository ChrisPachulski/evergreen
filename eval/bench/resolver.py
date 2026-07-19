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
        records = stages.get("prongs_escalated") or stages.get("prongs")
        if isinstance(records, list) and len(records) == 3 and _prongs(stages) is None:
            return _abstain(stages, "one or more prong responses are missing required fields")
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
    if role is not None:
        if value.get("role") != role or type(value.get("cleared_bar")) is not bool:
            return None
    return value


def _validated_v2(stages):
    snap = _proof_record(_value(stages, "snap"))
    challenge = _value(stages, "challenge")
    blindspot = _value(stages, "blindspot")
    records = stages.get("prongs_escalated") or stages.get("prongs")
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


def plurality_v2(snap, prongs):
    """Return the unique plurality among snap and high-bar prongs; concessions do not dissent."""
    votes = [snap["verdict"], *(
        prong["verdict"] for prong in prongs if prong["cleared_bar"]
    )]
    counts = {verdict: votes.count(verdict) for verdict in set(votes)}
    highest = max(counts.values())
    winners = [verdict for verdict, count in counts.items() if count == highest]
    return winners[0] if len(winners) == 1 else None


def _counted_v2_records(snap, prongs):
    return [snap, *(prong for prong in prongs if prong["cleared_bar"])]


def _v2_evidence_requires_synthesis(snap, prongs):
    """Evidence sufficiency is an independent gate, not a response to ordinary dissent."""
    counted = _counted_v2_records(snap, prongs)
    return any(
        vote["proof"] != "direct" or vote["verdict"] == "unverified" or
        (vote["verdict"] == "inconsistent" and
         vote["category"] not in {"direct-mismatch", "over-promise"}) or
        (vote["verdict"] != "inconsistent" and vote["category"] is not None)
        for vote in counted
    )


def needs_synthesis_v2(stages):
    values = _validated_v2(stages)
    if values is None:
        return True
    snap, challenge, prongs, blindspot = values
    return (
        challenge["cracks"] or bool(blindspot["missed_angle"]) or
        "prongs_escalated" in stages or plurality_v2(snap, prongs) is None or
        _v2_evidence_requires_synthesis(snap, prongs)
    )


def resolve_v2(stages):
    """Resolve proof-bearing stages, preserving uncertainty as a semantic outcome."""
    values = _validated_v2(stages)
    if values is None:
        return _abstain_v2(stages, "trial record is incomplete")
    snap, challenge, prongs, blindspot = values
    contested = needs_synthesis_v2(stages)
    winner = plurality_v2(snap, prongs)
    counted = _counted_v2_records(snap, prongs)
    source = next((vote for vote in counted if vote["verdict"] == winner), snap)
    if contested:
        source = _proof_record(_value(stages, "synthesis"))
        if source is None:
            return _abstain_v2(stages, "synthesis response is missing required fields")

    verdict = source["verdict"]
    direct_consistent = (
        verdict == "consistent" and source["proof"] == "direct" and
        source["category"] is None
    )
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


def valid_screen_value(result):
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    record = _proof_record(result.get("value"), role=None)
    if record is None:
        return None
    if type(record.get("uncertain")) is not bool:
        return None
    if "uncertainty_reason" not in record:
        return None
    reason = record["uncertainty_reason"]
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        return None
    return record


def route_screen_v3(result):
    """Asymmetric router: may cheaply clear a boring negative, never a drift flag."""
    value = valid_screen_value(result)
    if value is None:
        return {"decision": "jury", "reason": "screen-invalid-or-abstained"}
    if value["verdict"] != "consistent":
        return {"decision": "jury", "reason": value["verdict"]}
    if value["proof"] != "direct":
        return {"decision": "jury", "reason": "non-direct-proof"}
    if value["category"] is not None:
        return {"decision": "jury", "reason": "category-present"}
    if value["uncertain"]:
        return {"decision": "jury", "reason": "screen-uncertain"}
    return {"decision": "clear", "reason": "direct-consistent"}


def resolve_v3(stages):
    """Resolve cascade stages: a validated screen auto-clears or defers to the v2 jury."""
    route = route_screen_v3(stages.get("screen"))
    if stages.get("route") != route:
        return _abstain_v2(stages, "stored route does not match recomputed route")
    if route["decision"] == "clear":
        value = valid_screen_value(stages.get("screen"))
        return {
            "final_status": "complete", "semantic_status": "decided",
            "final_verdict": "consistent", "verdict": "consistent",
            "category": None, "why": value["evidence"],
            "proof": value["proof"], "claim": value["claim"],
            "evidence": value["evidence"], "contested": False,
            "stages": stages,
        }
    jury_stages = stages.get("jury")
    # Jury path returns resolve_v2's envelope verbatim, whose "stages" is the inner v2 trail
    # only -- NOT this {screen, route, jury} wrapper. The caller (_judge_cascade_v3) overwrites
    # decision["stages"] with the full wrapper before persistence, so replay can recompute the
    # route and execution ledger from the stored row. "stages" is excluded from replay's compared
    # fields, so the asymmetry is invisible today; if "stages" is ever added to the compared or
    # persisted set, the wrapper must be reconstructed here too.
    return resolve_v2(jury_stages if isinstance(jury_stages, dict) else {})


def resolve(stages, resolver_id):
    if resolver_id == "v1":
        return resolve_v1(stages)
    if resolver_id == "v2":
        return resolve_v2(stages)
    if resolver_id == "v3":
        return resolve_v3(stages)
    raise ValueError("resolver must be v1, v2, or v3")
