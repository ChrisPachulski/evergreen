#!/usr/bin/env python3
"""Score a flourish rewrite against a fixture's ground truth. Pure stdlib, standalone.

Usage:
    python3 score.py --fixture <fixture-dir> --result <rewritten-readme> [--cuts <json>] [--json]

The fixture dir holds `ground-truth.json` (schema_version 1: readme, sections[],
claims[], voice, traps[]) plus the source README and any files a rewrite may link
to. backed=false claim rows carry the forbidden capability's keyword stems in
`keywords` (e.g. ["parallel", "thread", "concurren"]) and keep any human
annotation in `note` — never in `text`, so matching keys on the capability, not
on annotation words. `--cuts` is an optional cuts declaration — a path to, or an
inline, JSON list of {"heading", "reason"} rows; a row only counts if its reason
is non-empty, at least 10 characters, and not a forbidden non-reason ("trimmed
for length", "trimmed", "for length", "brevity", "shortened" — case/whitespace-
insensitive; hard-goals/flourish.md goal 2). Absent a declaration, any missing
section fails conservation.

Four gates, per skills/evergreen/hard-goals/flourish.md. Every check is a grep or
a count — no model call, ever. The frozen contract is the external arbiter.

1. conservation (GATES): every ground-truth `##`/`###` section must be (a) a
   heading in the result, (b) inside a `<details>` block, or in a linked
   fixture-relative file that exists AND contains the heading text, or (c) on
   the cuts declaration. only_home=true sections accept only (a) or (b) — a
   declared cut still fails them (sole copy must survive).

   A heading alone is NOT conservation. Demote means "move, VERBATIM"
   (commands/flourish.md), so every candidate home — the kept section in the
   result, the <details> block, or the linked file — must also hold the
   section's BODY: the source section's body text (heading to the next heading;
   subsections are tracked as their own rows) is tokenized ([A-Za-z0-9_]+,
   lowercased, tokens under 3 chars dropped) and >= 70% of its unique tokens
   must appear in the candidate home. Documented bound: sections whose source
   body has fewer than 10 unique tokens keep plain heading-match semantics —
   there is too little text for overlap to mean anything.

   Link targets are guarded: a link only counts as a conservation home if it
   resolves to an existing, non-empty file INSIDE the fixture dir that is not
   the fixture's own source readme, not any *.json (ground-truth.json is the
   answer key, not a home), and nothing under the fixture's golden/ subtree
   (the golden outputs are answer keys too). Self-links, answer-key links,
   dead links, empty files, and paths escaping the fixture all leave the
   section unaccounted.
2. truth (GATES): candidate claims are extracted from the result (feature
   bullets, badge alt-texts, sentences containing supports/provides/handles —
   the -s and stem forms) and compared against ground-truth claims[]. If a
   backed=false claim appears in the result — exact/substring or by keywords —
   the gate FAILS.

   What the truth gate CAN catch:
     - a backed=false claim restated verbatim or near-verbatim (case- and
       whitespace-insensitive substring after markup stripping);
     - a keyword-stem hit on the forbidden capability: the claim's `keywords`
       stems are matched, case-insensitively, inside every single
       sentence/bullet/badge-alt unit of the result that is NOT present in the
       source readme; >= 2 stems in one unit fire the claim (>= 1 when the
       ground truth gives only one stem). Natural phrasing ("processes files
       in parallel across multiple threads") fires because the stems name the
       capability, not the annotation;
     - for legacy backed=false rows without `keywords`: the old derived-keyword
       fallback (content tokens of the claim text, length >= 4, stopwords
       dropped, stemmed to 6 chars, all landing on one result line; >= 2
       required).
   What it CANNOT catch:
     - synonym paraphrases sharing no keyword stem ("fans work out across
       cores" for the parallel trap);
     - claims negated or numerically altered;
     - a claim split across units so no single sentence carries >= 2 stems
       ("fans work out in parallel. Each file gets its own thread.") —
       per-unit matching is deliberate; document-level pooling would
       false-positive on truthful text;
     - claims inside fenced code blocks — fences are skipped deliberately,
       since truthful demos (a fence showing --parallel being refused) would
       otherwise false-positive;
     - fabricated claims with no backed=false row in the ground truth — those
       are reported as "unvetted new claims" for hand audit and never gate;
     - claims smuggled into images or rendered assets.
3. face (GATES): five individually-reported checks — centered hero before the
   first prose line, tagline/epigraph line distinct from the value-prop
   sentence, badge row in the first 30 lines, prose opener before any
   architecture-ish heading, an earned visual (non-badge image, ascii diagram
   in a fence, or a <details> block). Gate passes at >= 4 of 5 AND hero must
   be one of them.
4. voice (ADVISORY, never gates): if the source had a hook, report whether it
   survived the first 5 non-blank lines (kept / flattened at >= 60% token
   overlap / absent), plus a crude proxy (first/second-person sentence count,
   imperative section intros). Reported for humans; exit code unaffected.

Exit codes: 0 = all gates pass, 2 = any gate fails, 1 = operational error.
`--json` prints a machine document (schema_version, per-gate booleans,
per-check details, advisory voice block) instead of the human report.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for",
    "with", "over", "under", "from", "into", "onto", "via", "by", "at",
    "as", "is", "are", "was", "were", "be", "been", "being", "it", "its",
    "this", "that", "these", "those", "you", "your", "we", "our", "not",
    "no", "can", "will", "all", "any", "than", "then", "when", "which",
}
TRIGGER_VERBS = re.compile(r"\b(support|supports|provide|provides|handle|handles)\b", re.I)
ARCH_STEMS = ("architect", "internal", "design", "module", "component",
              "layout", "structur", "director")
IMPERATIVE_VERBS = {
    "run", "install", "add", "use", "try", "start", "clone", "build",
    "create", "open", "set", "drop", "grab", "import", "call", "check",
    "see", "read", "write", "make", "edit", "pass", "pip", "point", "ship",
}
HERO_RE = re.compile(r"<(?:h1|div)\b[^>]*align\s*=\s*['\"]?center", re.I)
FIRST_SECOND_PERSON = re.compile(r"\b(i|we|you|your|yours|our|ours|my|me|us)\b", re.I)


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def norm(s):
    return re.sub(r"\s+", " ", str(s).strip()).lower()


def strip_markup(s):
    s = re.sub(r"<[^>]+>", " ", str(s))
    s = re.sub(r"[*_`]", " ", s)
    return norm(s)


def heading_title(h):
    return norm(re.sub(r"^#{1,6}\s*", "", str(h).strip()))


def tokens(s):
    return re.findall(r"[a-z0-9']+", str(s).lower())


def claim_keywords(s):
    return {t[:6] for t in tokens(s) if len(t) >= 4 and t not in STOPWORDS}


def line_stems(s):
    return {t[:6] for t in tokens(strip_markup(s))}


def fence_mask(lines):
    mask, fence = [], False
    for line in lines:
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            mask.append(True)
            fence = not fence
        else:
            mask.append(fence)
    return mask


def fenced_blocks(lines):
    blocks, cur, fence = [], [], False
    for line in lines:
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            if fence:
                blocks.append("\n".join(cur))
                cur = []
            fence = not fence
            continue
        if fence:
            cur.append(line)
    return blocks


def parse_headings(lines, mask):
    """[(line_idx, level, normalized_title, inside_details)] for ATX headings."""
    out, depth = [], 0
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        s = line.strip()
        depth += len(re.findall(r"<details\b", s, re.I))
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", s)
        if m:
            out.append((i, len(m.group(1)), norm(m.group(2)), depth > 0))
        depth = max(0, depth - len(re.findall(r"</details>", s, re.I)))
    return out


def details_block_texts(text):
    return [norm(m.group(0))
            for m in re.finditer(r"<details\b.*?</details>", text, re.I | re.S)]


def linked_relative_paths(text):
    targets = []
    for m in re.finditer(r"\[[^\]]*\]\(([^)\s]+)(?:\s[^)]*)?\)", text):
        targets.append(m.group(1))
    for m in re.finditer(r"""<a\s[^>]*href=["']([^"']+)["']""", text, re.I):
        targets.append(m.group(1))
    out = []
    for t in targets:
        t = t.split("#", 1)[0].split("?", 1)[0].strip()
        if not t or t.startswith("//") or re.match(r"^[a-z][a-z0-9+.-]*:", t, re.I):
            continue
        if t not in out:
            out.append(t)
    return out


ITALIC_ONLY = re.compile(
    r"^(?:<em>.+</em>|<i>.+</i>|\*[^*].*[^*]\*|_[^_].*[^_]_)$", re.I | re.S)


def is_italic_only(line):
    s = line.strip()
    s = re.sub(r"^<p\b[^>]*>", "", s, flags=re.I).strip()
    s = re.sub(r"</p>$", "", s, flags=re.I).strip()
    return bool(ITALIC_ONLY.match(s))


def is_prose(line):
    t = line.strip()
    if not t:
        return False
    if t.startswith(("#", "<", "!", ">", "|", "```", "~~~", "[")):
        return False
    if re.match(r"^(?:[-*+]\s|\d+[.)]\s)", t):
        return False
    if re.match(r"^(?:-{3,}|\*{3,}|={3,})$", t):
        return False
    if is_italic_only(t):
        return False
    return True


# --- gate 1: conservation -------------------------------------------------

BODY_OVERLAP_MIN = 0.70     # >= 70% of the source body's unique tokens must survive
BODY_TOKENS_MIN = 10        # under this, the body is too thin — heading match suffices

FORBIDDEN_CUT_REASONS = {
    "trimmed for length", "trimmed", "for length", "brevity", "shortened",
}
CUT_REASON_MIN_CHARS = 10


def body_tokens(text):
    """Unique lowercase [A-Za-z0-9_]+ tokens of length >= 3."""
    return {m.group(0).lower() for m in re.finditer(r"[A-Za-z0-9_]+", str(text))
            if len(m.group(0)) >= 3}


def body_overlap(src_toks, candidate_text):
    if not src_toks:
        return 1.0
    return len(src_toks & body_tokens(candidate_text)) / len(src_toks)


def source_section_bodies(source_text):
    """title -> body text, heading to the NEXT heading of any level.

    Subsections are ground-truth rows of their own, so a parent's body is its
    direct content only — a gutted subsection fails its own row, not the
    parent's.
    """
    lines = source_text.splitlines()
    mask = fence_mask(lines)
    headings = parse_headings(lines, mask)
    bodies = {}
    for k, (idx, _level, title, _inside) in enumerate(headings):
        end = headings[k + 1][0] if k + 1 < len(headings) else len(lines)
        bodies.setdefault(title, "\n".join(lines[idx + 1:end]))
    return bodies


def result_section_bodies(result_text):
    """title -> [(inside_details, body_text)] per occurrence in the result.

    A result occurrence's body runs to the next non-details heading of the
    same or higher level, so a rewrite keeps credit for content it files under
    new, deeper subheadings or trailing <details> blocks.
    """
    lines = result_text.splitlines()
    mask = fence_mask(lines)
    headings = parse_headings(lines, mask)
    out = {}
    for k, (idx, level, title, inside) in enumerate(headings):
        end = len(lines)
        for j in range(k + 1, len(headings)):
            j_idx, j_level, _t, j_inside = headings[j]
            if j_level <= level and not j_inside:
                end = j_idx
                break
        out.setdefault(title, []).append((inside, "\n".join(lines[idx + 1:end])))
    return out


def resolve_linked_homes(gt, fixture, result_text):
    """rel-path -> normalized content, for links that may serve as homes.

    Excluded (never a conservation home): the fixture's own source readme
    (a self-link conserves nothing), any *.json (ground-truth.json is the
    answer key), anything under the fixture's golden/ subtree (the golden
    outputs are answer keys too), any path resolving outside the fixture dir,
    dead links, and empty files.
    """
    linked = {}
    fixture_res = fixture.resolve()
    source_res = (fixture / gt["readme"]).resolve()
    golden_res = (fixture / "golden").resolve()
    readme_parent = (fixture / gt["readme"]).parent
    for rel in linked_relative_paths(result_text):
        for base_dir in (fixture, readme_parent):
            p = base_dir / rel
            if not p.is_file():
                continue
            rp = p.resolve()
            if rp == source_res or rp.suffix.lower() == ".json":
                continue
            if rp == golden_res or golden_res in rp.parents:
                continue
            if not rp.is_relative_to(fixture_res):
                continue
            try:
                content = norm(p.read_text(errors="replace"))
            except OSError:
                continue
            if content:
                linked[rel] = content
                break
    return linked


def cut_reason_rejection(reason):
    """None if the reason is acceptable, else why it is rejected."""
    r = norm(reason).strip(" .!?,;:")
    if not r:
        return "empty reason"
    if r in FORBIDDEN_CUT_REASONS:
        return f"forbidden reason {reason.strip()!r}"
    if len(r) < CUT_REASON_MIN_CHARS:
        return f"reason under {CUT_REASON_MIN_CHARS} characters: {reason.strip()!r}"
    return None


def gate_conservation(gt, fixture, source_text, result_text, cuts):
    block_texts = details_block_texts(result_text)
    result_bodies = result_section_bodies(result_text)
    source_bodies = source_section_bodies(source_text)
    linked = resolve_linked_homes(gt, fixture, result_text)

    cut_map, rejected_cuts = {}, {}
    for row in cuts:
        h = heading_title(row.get("heading", ""))
        if not h:
            continue
        why = cut_reason_rejection(row.get("reason", ""))
        if why is None:
            cut_map[h] = row
        else:
            rejected_cuts[h] = {"reason": str(row.get("reason", "")), "why": why}

    rows = []
    for sec in gt.get("sections", []):
        title = heading_title(sec.get("heading", ""))
        only = bool(sec.get("only_home"))
        src_toks = body_tokens(source_bodies.get(title, ""))
        lenient = len(src_toks) < BODY_TOKENS_MIN  # documented bound: thin body

        disp, home, best = None, None, None  # best = (overlap, kind, home)

        def consider(kind, cand_home, candidate_text):
            nonlocal disp, home, best
            if disp is not None:
                return
            ov = body_overlap(src_toks, candidate_text)
            if lenient or ov >= BODY_OVERLAP_MIN:
                disp, home = kind, cand_home
            elif best is None or ov > best[0]:
                best = (ov, kind, cand_home)

        for inside, body in result_bodies.get(title, []):
            consider("details" if inside else "present", None, body)
        for bt in block_texts:
            if title in bt:
                consider("details", None, bt)
        for rel, content in linked.items():
            if title in content:
                consider("linked", rel, content)

        rejected = None
        if disp is None:
            if title in cut_map:
                disp = "cut-declared-but-only-home" if only else "cut"
                if disp == "cut":
                    home = norm(cut_map[title].get("reason", ""))
            elif best is not None:
                disp = "body-gutted"
                home = best[2] if best[1] == "linked" else best[1]
                rejected = (f"heading found ({best[1]}) but only "
                            f"{best[0]:.0%} of the source body survives"
                            f" (needs >= {BODY_OVERLAP_MIN:.0%})")
            elif title in rejected_cuts:
                disp = "cut-reason-rejected"
                rejected = (f"{rejected_cuts[title]['why']} — "
                            f"declared reason: {rejected_cuts[title]['reason']!r}")
            else:
                disp = "missing"
        ok = disp in ("present", "details", "linked", "cut")
        row = {"heading": sec.get("heading"), "only_home": only,
               "disposition": disp, "home": home, "ok": ok}
        if rejected:
            row["detail"] = rejected
        rows.append(row)
    return {
        "total": len(rows),
        "accounted": sum(1 for r in rows if r["ok"]),
        "sections": rows,
    }, all(r["ok"] for r in rows)


# --- gate 2: truth ----------------------------------------------------------

def extract_candidates(result_text):
    """Feature bullets, badge alt-texts, and supports/provides/handles sentences."""
    cands = []
    lines = result_text.splitlines()
    mask = fence_mask(lines)
    for i, line in enumerate(lines):
        if mask[i]:
            continue
        s = line.strip()
        m = re.match(r"^[-*+]\s+(.+)$", s)
        if m:
            cands.append({"kind": "bullet", "line": i + 1, "text": m.group(1)})
        for alt in re.findall(r"!\[([^\]]+)\]", s):
            cands.append({"kind": "badge-alt", "line": i + 1, "text": alt})
        for m in re.finditer(r"""<img\s[^>]*alt=["']([^"']+)["']""", s, re.I):
            cands.append({"kind": "badge-alt", "line": i + 1, "text": m.group(1)})
        if not s.startswith("#"):
            for sent in re.split(r"(?<=[.!?])\s+", s):
                if TRIGGER_VERBS.search(sent):
                    cands.append({"kind": "sentence", "line": i + 1,
                                  "text": sent.strip()})
    return cands


def claim_matches_text(claim_text, other_text):
    c, o = strip_markup(claim_text), strip_markup(other_text)
    if c and o and (c in o or o in c):
        return True
    kws = claim_keywords(claim_text)
    return len(kws) >= 2 and kws <= line_stems(other_text)


def truth_units(result_text):
    """[(line_no, text)] — every sentence, bullet, and badge alt of the result.

    Wrapped paragraphs are joined before sentence-splitting so a claim reflowed
    across lines is still one unit. Fenced code is skipped.
    """
    lines = result_text.splitlines()
    mask = fence_mask(lines)
    units = []
    para, para_start = [], None

    def flush():
        nonlocal para, para_start
        if para:
            joined = " ".join(para)
            for sent in re.split(r"(?<=[.!?])\s+", joined):
                if sent.strip():
                    units.append((para_start, sent.strip()))
        para, para_start = [], None

    for i, line in enumerate(lines):
        if mask[i]:
            flush()
            continue
        s = line.strip()
        for alt in re.findall(r"!\[([^\]]+)\]", s):
            units.append((i + 1, alt))
        for m in re.finditer(r"""<img\s[^>]*alt=["']([^"']+)["']""", s, re.I):
            units.append((i + 1, m.group(1)))
        if not s:
            flush()
            continue
        m = re.match(r"^[-*+]\s+(.+)$", s)
        if m:
            flush()
            units.append((i + 1, m.group(1)))
            continue
        if s.startswith(("#", "<", ">", "|", "```", "~~~")):
            flush()
            if s.startswith(("|", ">")):
                units.append((i + 1, s))
            elif s.startswith("#"):
                # A capability claim in a heading is at least as loud as a bullet.
                heading_text = s.lstrip("#").strip()
                if heading_text:
                    units.append((i + 1, heading_text))
            elif s.startswith("<"):
                # Tag-wrapped prose is still prose; strip markup and scan what survives.
                prose = strip_markup(s)
                if prose:
                    units.append((i + 1, prose))
            continue
        if para_start is None:
            para_start = i + 1
        para.append(s)
    flush()
    return units


def gate_truth(gt, source_text, result_text):
    src_norm = strip_markup(source_text)
    result_lines = result_text.splitlines()
    claims = gt.get("claims", [])
    unbacked = [c for c in claims if not c.get("backed")]
    units = truth_units(result_text)

    fabrications = []
    result_norm = strip_markup(result_text)
    for c in unbacked:
        text = c.get("text", "")
        c_norm = strip_markup(text)
        stems = [str(k).lower() for k in c.get("keywords", []) if str(k).strip()]
        method, at_line, evidence = None, None, None
        if c_norm and c_norm in result_norm:
            method = "exact"
        if method is None and stems:
            # keyword-stem path: >= 2 stems (>= 1 if only one is given) inside
            # a single sentence/bullet/badge-alt unit NOT present in the source
            need = 2 if len(stems) >= 2 else 1
            for line_no, unit in units:
                u_norm = strip_markup(unit)
                if not u_norm or u_norm in src_norm:
                    continue
                if sum(1 for k in stems if k in u_norm) >= need:
                    method, at_line, evidence = "keywords", line_no, unit
                    break
        if method is None and not stems:
            # legacy fallback for rows without keywords: derived claim stems
            kws = claim_keywords(text)
            if len(kws) >= 2:
                for i, line in enumerate(result_lines):
                    if kws <= line_stems(line):
                        method, at_line, evidence = "keywords", i + 1, line.strip()
                        break
        if method:
            fabrications.append({"id": c.get("id"), "text": text,
                                 "method": method, "line": at_line,
                                 "evidence_line": evidence})

    new_cands = [c for c in extract_candidates(result_text)
                 if strip_markup(c["text"]) and strip_markup(c["text"]) not in src_norm]
    unvetted = [c for c in new_cands
                if not any(claim_matches_text(gc.get("text", ""), c["text"])
                           for gc in claims)]
    return {
        "unbacked_total": len(unbacked),
        "fired": len(fabrications),
        "fabrications": fabrications,
        "new_candidates": len(new_cands),
        "unvetted_new_claims": unvetted,
    }, not fabrications


# --- gate 3: face -----------------------------------------------------------

def gate_face(result_text):
    lines = result_text.splitlines()
    mask = fence_mask(lines)

    prose_idx = next((i for i, l in enumerate(lines)
                      if not mask[i] and is_prose(l)), None)
    hero_idx = next((i for i, l in enumerate(lines)
                     if not mask[i] and HERO_RE.search(l)), None)
    hero_ok = hero_idx is not None and (prose_idx is None or hero_idx < prose_idx)

    headings = parse_headings(lines, mask)
    first_h2_idx = next((h[0] for h in headings if h[1] >= 2), None)
    region_end = first_h2_idx if first_h2_idx is not None else len(lines)
    tagline_idx = next((i for i in range(region_end)
                        if not mask[i] and is_italic_only(lines[i])), None)
    tagline_text = strip_markup(lines[tagline_idx]) if tagline_idx is not None else None
    prose_text = strip_markup(lines[prose_idx]) if prose_idx is not None else None
    tagline_ok = (tagline_text is not None and prose_text is not None
                  and tagline_text != prose_text)

    badge_idx = None
    for i, line in enumerate(lines[:30]):
        if mask[i]:
            continue
        if ("shields.io" in line or "[![" in line
                or re.search(r"!\[[^\]]*\]\([^)]*badge", line, re.I)):
            badge_idx = i
            break
    badges_ok = badge_idx is not None

    arch_idx = next((h[0] for h in headings
                     if any(t.startswith(ARCH_STEMS) for t in tokens(h[2]))), None)
    opener_ok = prose_idx is not None and (arch_idx is None or prose_idx < arch_idx)

    visual_kind = None
    for m in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)", result_text):
        url = m.group(1).lower()
        if "shields.io" not in url and "badge" not in url:
            visual_kind = "image"
            break
    if visual_kind is None:
        for m in re.finditer(r"""<img\s[^>]*src=["']([^"']+)["']""", result_text, re.I):
            url = m.group(1).lower()
            if "shields.io" not in url and "badge" not in url:
                visual_kind = "image"
                break
    if visual_kind is None:
        diagram = re.compile(r"-->|<--|\+--|--\+|==>|[─│┌┐"
                             r"└┘├┤┬┴┼"
                             r"═║╔╗╚╝]")
        if any(diagram.search(b) for b in fenced_blocks(lines)):
            visual_kind = "ascii-diagram"
    if visual_kind is None and re.search(r"<details\b", result_text, re.I):
        visual_kind = "details-demo"
    visual_ok = visual_kind is not None

    checks = {
        "hero": {"ok": hero_ok, "line": None if hero_idx is None else hero_idx + 1},
        "tagline": {"ok": tagline_ok,
                    "line": None if tagline_idx is None else tagline_idx + 1},
        "badges": {"ok": badges_ok,
                   "line": None if badge_idx is None else badge_idx + 1},
        "opener": {"ok": opener_ok,
                   "line": None if prose_idx is None else prose_idx + 1},
        "visual": {"ok": visual_ok, "kind": visual_kind},
    }
    score = sum(1 for c in checks.values() if c["ok"])
    # hard-goals/flourish.md goal 4 requires hero AND tagline ("both greps hit");
    # the wider 4-of-5 bar is this eval's stricter overlay, never a substitute.
    return {"checks": checks, "score": score,
            "hero_mandatory": True, "tagline_mandatory": True}, \
        hero_ok and tagline_ok and score >= 4


# --- gate 4: voice (advisory only) -----------------------------------------

def report_voice(gt, result_text):
    out = {"advisory": True,
           "note": "advisory only — never gates, never affects the exit code"}
    lines = result_text.splitlines()
    voice = gt.get("voice") or {}
    hook_line = voice.get("hook_line")

    if voice.get("has_hook") and hook_line:
        opening = [l for l in lines if l.strip()][:5]
        hook = strip_markup(hook_line)
        hook_toks = set(tokens(hook))
        status, best = "absent", 0.0
        for l in opening:
            ln = strip_markup(l)
            if hook and hook in ln:
                status, best = "kept", 1.0
                break
            if hook_toks:
                best = max(best, len(hook_toks & set(tokens(ln))) / len(hook_toks))
        if status != "kept":
            if best >= 0.6:
                status = "flattened"
            elif hook and hook in strip_markup(result_text):
                status = "flattened"  # survived verbatim but demoted out of the opening
        out["hook"] = {"status": status, "overlap": round(best, 2),
                       "hook_line": hook_line}
    else:
        out["hook"] = {"status": "n/a", "overlap": None, "hook_line": None}

    mask = fence_mask(lines)
    person = 0
    for i, line in enumerate(lines):
        if mask[i] or not is_prose(line):
            continue
        for sent in re.split(r"(?<=[.!?])\s+", line.strip()):
            if sent and FIRST_SECOND_PERSON.search(sent):
                person += 1
    headings = parse_headings(lines, mask)
    intros_total, imperative = 0, 0
    for idx, level, _title, _in_details in headings:
        if level not in (2, 3):
            continue
        intro = next((lines[j].strip() for j in range(idx + 1, len(lines))
                      if lines[j].strip() and not mask[j]), None)
        if intro is None:
            continue
        intros_total += 1
        toks = tokens(intro)
        if toks and toks[0] in IMPERATIVE_VERBS:
            imperative += 1
    out["proxy"] = {"first_second_person_sentences": person,
                    "imperative_intros": imperative,
                    "section_intros": intros_total}
    return out


# --- io ---------------------------------------------------------------------

def load_cuts(arg):
    if arg is None:
        return []
    raw = arg
    try:
        p = Path(arg)
        if p.is_file():
            raw = p.read_text()
    except OSError:
        pass
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        die(f"--cuts is neither a readable JSON file nor inline JSON: {arg!r}")
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        die('--cuts must be a JSON list of {"heading", "reason"} objects')
    return data


def load_ground_truth(fixture):
    gt_path = fixture / "ground-truth.json"
    if not gt_path.is_file():
        die(f"no ground-truth.json in {fixture}")
    try:
        gt = json.loads(gt_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        die(f"cannot parse {gt_path}: {e}")
    if gt.get("schema_version") != SCHEMA_VERSION:
        die(f"unsupported ground-truth schema_version {gt.get('schema_version')!r}"
            f" (scorer speaks {SCHEMA_VERSION})")
    if not isinstance(gt.get("sections"), list) or "readme" not in gt:
        die(f"{gt_path} is missing required keys (readme, sections)")
    return gt


CHECK_ORDER = ("hero", "tagline", "badges", "opener", "visual")


def print_human(doc):
    mark = lambda ok: "✓" if ok else "✗"
    cons, truth, face, voice = (doc["conservation"], doc["truth"],
                                doc["face"], doc["voice"])
    g = doc["gates"]

    print(f"conservation: {'PASS' if g['conservation'] else 'FAIL'}"
          f" — {cons['accounted']}/{cons['total']} sections accounted for")
    for r in cons["sections"]:
        if not r["ok"]:
            extra = " (only_home — a declared cut cannot save it)" \
                if r["disposition"] == "cut-declared-but-only-home" else \
                (" (only_home)" if r["only_home"] else "")
            if r.get("detail"):
                extra += f" — {r['detail']}"
            print(f"  ✗ {r['heading']} — {r['disposition']}{extra}")

    print(f"truth:        {'PASS' if g['truth'] else 'FAIL'}"
          f" — {truth['fired']}/{truth['unbacked_total']} backed=false claims fired")
    for f in truth["fabrications"]:
        where = f" line {f['line']}" if f.get("line") else ""
        print(f"  ✗ {f['id']} ({f['method']}{where}): {f['text']}")
    if truth["unvetted_new_claims"]:
        print(f"  unvetted new claims (audit these by hand): "
              f"{len(truth['unvetted_new_claims'])}")
        for c in truth["unvetted_new_claims"]:
            print(f"  ? line {c['line']} [{c['kind']}] {c['text']}")

    parts = "  ".join(f"{name} {mark(face['checks'][name]['ok'])}"
                      for name in CHECK_ORDER)
    print(f"face:         {'PASS' if g['face'] else 'FAIL'}"
          f" — {face['score']}/5 ({parts}); hero mandatory")

    hook = voice["hook"]
    proxy = voice["proxy"]
    hook_bit = hook["status"] if hook["status"] == "n/a" else \
        f"hook {hook['status']} (overlap {hook['overlap']})"
    print(f"voice (advisory, never gates): {hook_bit};"
          f" 1st/2nd-person sentences: {proxy['first_second_person_sentences']};"
          f" imperative intros: {proxy['imperative_intros']}/{proxy['section_intros']}")
    print(f"verdict: {doc['verdict']}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Score a flourish rewrite against a fixture's ground truth.")
    ap.add_argument("--fixture", required=True, help="fixture dir with ground-truth.json")
    ap.add_argument("--result", required=True, help="path to the rewritten readme")
    ap.add_argument("--cuts", default=None,
                    help='path to, or inline, JSON list of {"heading", "reason"}')
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="emit the machine-readable document instead of the human report")
    args = ap.parse_args(argv)

    fixture = Path(args.fixture)
    if not fixture.is_dir():
        die(f"fixture dir not found: {fixture}")
    gt = load_ground_truth(fixture)
    source_path = fixture / gt["readme"]
    if not source_path.is_file():
        die(f"fixture readme not found: {source_path}")
    result_path = Path(args.result)
    if not result_path.is_file():
        die(f"result file not found: {result_path}")
    cuts = load_cuts(args.cuts)

    source_text = source_path.read_text(errors="replace")
    result_text = result_path.read_text(errors="replace")

    cons_detail, cons_ok = gate_conservation(gt, fixture, source_text,
                                             result_text, cuts)
    truth_detail, truth_ok = gate_truth(gt, source_text, result_text)
    face_detail, face_ok = gate_face(result_text)
    voice_detail = report_voice(gt, result_text)

    all_ok = cons_ok and truth_ok and face_ok
    doc = {
        "schema_version": SCHEMA_VERSION,
        "fixture": str(fixture),
        "result": str(result_path),
        "gates": {"conservation": cons_ok, "truth": truth_ok, "face": face_ok},
        "verdict": "PASS" if all_ok else "FAIL",
        "conservation": cons_detail,
        "truth": truth_detail,
        "face": face_detail,
        "voice": voice_detail,
    }
    if args.as_json:
        print(json.dumps(doc, indent=2))
    else:
        print_human(doc)
    sys.exit(0 if all_ok else 2)


if __name__ == "__main__":
    main()
