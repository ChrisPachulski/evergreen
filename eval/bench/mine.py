#!/usr/bin/env python3
"""Mine wild coupled code + doc-comment changes from real repos — CoDocBench's recipe, any language.

CoDocBench (arXiv:2502.00519) shipped Python only. Its recipe generalizes: walk a repo's commits,
and for every function whose doc-comment AND body changed in the same commit, emit the before/after
pair. `codocbench_to_jsonl.py` then derives drift candidates and `validate_labels.py` label-validates
them by three-LLM majority vote — the exact pipeline we already ran for Python. Mining is allowed to
be noisy: the validation vote is the quality gate (it rejected ~78% of Python heuristic positives).

Function boundaries come from Lizard (bundled with PyDriller; supports C/C++/Go/Rust/TS/JS/…); the
doc-comment is the contiguous run of comment lines directly above the function (godoc `//`, rustdoc
`///`, JSDoc/Doxygen `/** */`). A coupled change = a function present before and after a commit with
BOTH its doc text and its body text changed.

Output is CoDocBench schema (one JSON row per coupled change), so it flows through unchanged:
  {"owner","project","file","function","language","commit",
   "version_data":[{"docstring","code"},{"docstring","code"}],   # [old, new]
   "whitespace_only_docstring": bool}

  # needs the mining venv (pydriller); not an evergreen runtime dep
  python3 eval/bench/mine.py --lang typescript --repos repos.txt --out mined-ts.jsonl --max-per-repo 400
  python3 eval/bench/mine.py --selftest
"""
import argparse
import json
import re
import sys
from pathlib import Path

EXT = {"typescript": ".ts", "javascript": ".js", "go": ".go", "rust": ".rs", "c": ".c", "cpp": ".cpp"}
SRC_EXTS = {"typescript": (".ts", ".tsx"), "javascript": (".js", ".mjs", ".jsx"), "go": (".go",),
            "rust": (".rs",), "c": (".c", ".h"), "cpp": (".cpp", ".cc", ".cxx", ".hpp", ".hh")}
# What counts as a DOC comment per language (block above the function). Go/Rust use line comments
# by convention (godoc, rustdoc); the C-family and JS-family use /** */ (JSDoc/Doxygen). A loose
# inline // above a TS function is NOT its API doc, so it must not qualify — else we feed the
# expensive validation vote noise.
def is_doc(block, lang):
    b = block.lstrip()
    if lang in ("go",):
        return b.startswith("//") and not b.startswith("//go:") and not b.startswith("// +build")
    if lang in ("rust",):
        return b.startswith("///") or b.startswith("//!") or b.startswith("/**")
    return b.startswith("/**")            # typescript, javascript, c, cpp → JSDoc/Doxygen only


SKIP_PATH = re.compile(r"(^|/)(test|tests|__tests__|spec|vendor|node_modules|third_party|examples?)/"
                       r"|\.(test|spec)\.", re.I)


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _is_comment_line(s):
    s = s.strip()
    return bool(s) and (s.startswith("//") or s.startswith("/*") or s.startswith("*")
                        or s.endswith("*/"))


def doc_above(lines, start_line):
    """Contiguous comment lines directly above a function (1-indexed start_line). Blank gap stops."""
    out, i = [], start_line - 2
    while i >= 0 and _is_comment_line(lines[i]):
        out.append(lines[i]); i -= 1
    return "\n".join(reversed(out))


def documented_funcs(source, lang):
    """{signature-key: (doc, code, name)} for every DOC-commented, named function in a file."""
    import lizard
    lines = source.splitlines()
    try:
        r = lizard.analyze_file.analyze_source_code("x" + EXT[lang], source)
    except Exception:
        return {}
    out = {}
    for f in r.function_list:
        if f.name in ("(anonymous)", "", None) or f.start_line < 1 or f.end_line > len(lines) \
                or f.end_line < f.start_line:
            continue
        doc = doc_above(lines, f.start_line)
        if not is_doc(doc, lang):                 # only real API docs, not stray inline comments
            continue
        code = "\n".join(lines[f.start_line - 1:f.end_line])
        out[f.long_name] = (doc, code, f.name)    # long_name carries the signature (disambiguates)
    return out


def coupled_changes(before, after, lang):
    """Functions in both versions where BOTH the doc-comment and the body text changed."""
    b, a = documented_funcs(before, lang), documented_funcs(after, lang)
    for key in b.keys() & a.keys():
        (db, cb, _), (da, ca, name) = b[key], a[key]
        if not norm(db) or not norm(da):                 # both versions must carry a doc
            continue
        if norm(db) != norm(da) and norm(cb) != norm(ca):   # doc AND code moved
            yield name, db, cb, da, ca


def mine_repo(url, lang, max_per_repo):
    from pydriller import Repository
    exts = SRC_EXTS[lang]
    owner, project = url.rstrip("/").replace(".git", "").split("/")[-2:]
    rows, n = [], 0
    for commit in Repository(url, only_modifications_with_file_types=list(exts)).traverse_commits():
        for mf in commit.modified_files:
            if (mf.filename.lower().endswith(exts) is False or mf.source_code is None
                    or mf.source_code_before is None or SKIP_PATH.search(mf.new_path or "")):
                continue
            try:
                changes = list(coupled_changes(mf.source_code_before, mf.source_code, lang))
            except Exception:
                continue
            for func, db, cb, da, ca in changes:
                if len(ca) > 6000 or len(ca) < 15:
                    continue
                rows.append({"owner": owner, "project": project, "file": mf.new_path,
                             "function": func, "language": lang, "commit": commit.hash[:12],
                             "version_data": [{"docstring": db, "code": cb},
                                              {"docstring": da, "code": ca}],
                             "whitespace_only_docstring": not norm(db) or not norm(da)})
                n += 1
                if n >= max_per_repo:
                    return rows
    return rows


def selftest():
    cases = [
        ("typescript", "/** Adds two numbers. */\nfunction add(a: number, b: number): number { return a + b; }\n",
         "/** Adds two numbers and logs. */\nfunction add(a: number, b: number): number { console.log(a); return a + b; }\n"),
        ("go", "// Add sums two ints.\nfunc Add(a, b int) int { return a + b }\n",
         "// Add sums two ints and logs.\nfunc Add(a, b int) int { println(a); return a + b }\n"),
        ("rust", "/// Adds two numbers.\nfn add(a: i32, b: i32) -> i32 { a + b }\n",
         "/// Adds two numbers, logging.\nfn add(a: i32, b: i32) -> i32 { println!(\"{}\", a); a + b }\n"),
        ("c", "/** Add two ints. */\nint add(int a, int b) { return a + b; }\n",
         "/** Add two ints, verbose. */\nint add(int a, int b) { printf(\"x\"); return a + b; }\n"),
    ]
    for lang, before, after in cases:
        hits = [c[0] for c in coupled_changes(before, after, lang)]
        assert hits == ["add"] or hits == ["Add"], (lang, hits)
        # doc-only change (body identical) must NOT be coupled
        doc_only = after.replace("console.log(a); ", "").replace("println(a); ", "") \
                        .replace("println!(\"{}\", a); ", "").replace("printf(\"x\"); ", "")
        assert not list(coupled_changes(before, doc_only, lang)), (lang, "doc-only leaked")
        # body-only change (doc identical) must NOT be coupled
        body_only = before.split("\n", 1)[0] + "\n" + after.split("\n", 1)[1]
        assert not list(coupled_changes(before, body_only, lang)), (lang, "body-only leaked")
    print("mine selftest ok — coupled detection fires only when doc AND body both change")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", required=True, choices=list(EXT))
    ap.add_argument("--repos", required=True, help="file with one git URL per line")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-per-repo", type=int, default=400)
    a = ap.parse_args()
    urls = [l.strip() for l in Path(a.repos).read_text().splitlines()
            if l.strip() and not l.startswith("#")]
    seen, total = set(), 0
    with open(a.out, "w") as f:
        for url in urls:
            try:
                rows = mine_repo(url, a.lang, a.max_per_repo)
            except Exception as e:
                print(f"  ! {url}: {e}", file=sys.stderr, flush=True); continue
            kept = 0
            for r in rows:
                sig = (r["function"], norm(r["version_data"][0]["code"]),
                       norm(r["version_data"][1]["code"]))
                if sig in seen:
                    continue
                seen.add(sig)
                f.write(json.dumps(r) + "\n"); f.flush(); kept += 1
            total += kept
            print(f"  {url.split('/')[-1]:30} +{kept} (total {total})", flush=True)
    print(f"mined {total} coupled changes -> {a.out}")


if __name__ == "__main__":
    main()
