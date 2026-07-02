#!/usr/bin/env python3
"""Execute a self-contained test that encodes a doc's claim, return pass/fail/skip.

evergreen's precision leak is false positives on judgment calls. The fix Cascade uses
(arXiv:2604.19400, their Phase 2: 0.82->0.88 precision) is execution: turn "I think the doc
lies" into "here is a test of the doc's claim that passes/fails against the real code."

Contract (kept deliberately uniform so adding a language is one table row, not a harness):
the caller hands us ONE self-contained source file in the target language that includes the
code under test AND asserts what the DOCUMENTATION claims, exiting 0 iff the claim holds. We
write it to a scratch dir and run it. Exit 0 -> "pass" (doc is consistent, false positive
killed). Nonzero from the RUN step -> "fail" (doc drift, proven by execution). A compile error
or missing toolchain -> "skip" (inconclusive; the caller falls through to the audit stage) —
never "fail", because a broken synthesized test must not manufacture drift.

  python3 eval/bench/prove.py --selftest   # runs a known-pass and known-fail Python case

ponytail: local-toolchain execution with a tmpdir + timeout, not a container. These are tiny
synthesized programs over already-cloned public-repo snippets; the residual risk is a
model-authored test doing something dumb in a temp dir. Upgrade path: set EVAL_PROVE_DOCKER=1
to route each run through `docker run --network none` once a daemon is present (table carries
the image per language).
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# lang -> (source filename, needs-binary, [argv steps with {F}=file {X}=exe], docker image).
# Multiple steps = check/compile then run; only the LAST step's exit code is the verdict. A
# nonzero from any earlier step is a SKIP not a FAIL — a broken synthesized test must never be
# read as drift. Interpreted langs get a syntax pre-check (py_compile / node --check / ruby -c /
# bash -n) using the same binary; compiled langs separate build from run. {F} is the source
# file, {X} a built binary in the same scratch dir.
LANGS = {
    "python":     ("t.py",    "python3", [["python3", "-I", "-m", "py_compile", "{F}"], ["python3", "-I", "{F}"]], "python:3-slim"),
    "javascript": ("t.mjs",   "node",    [["node", "--check", "{F}"], ["node", "{F}"]],  "node:20-slim"),
    "typescript": ("t.ts",    "node",    [["node", "--experimental-strip-types", "{F}"]], "node:22-slim"),
    "ruby":       ("t.rb",    "ruby",    [["ruby", "-c", "{F}"], ["ruby", "{F}"]],        "ruby:3-slim"),
    "bash":       ("t.sh",    "bash",    [["bash", "-n", "{F}"], ["bash", "{F}"]],        "bash:5"),
    "r":          ("t.R",     "Rscript", [["Rscript", "{F}"]],                            "r-base"),
    "go":         ("main.go", "go",      [["go", "build", "-o", "{X}", "{F}"], ["{X}"]],  "golang:1"),
    "rust":       ("t.rs",    "rustc",   [["rustc", "-O", "{F}", "-o", "{X}"], ["{X}"]],  "rust:1-slim"),
    "c":          ("t.c",     "cc",      [["cc", "{F}", "-o", "{X}"], ["{X}"]],           "gcc:latest"),
    "cpp":        ("t.cpp",   "c++",     [["c++", "{F}", "-o", "{X}"], ["{X}"]],          "gcc:latest"),
    "swift":      ("t.swift", "swift",   [["swift", "{F}"]],                              "swift:latest"),
}

# spellings that map onto a table key
ALIASES = {
    "js": "javascript", "node": "javascript", "ts": "typescript",
    "c++": "cpp", "cxx": "cpp", "cc": "c", "golang": "go", "rs": "rust",
    "rlang": "r", "sh": "bash", "shell": "bash", "py": "python",
}


def canon(language):
    lang = (language or "").strip().lower()
    return ALIASES.get(lang, lang)


def _has(step0_binary):
    return shutil.which(step0_binary) is not None


def available_langs():
    """Table languages whose toolchain is actually installed here."""
    return sorted(k for k, (_, needs, _, _) in LANGS.items() if _has(needs))


def run_test(language, test_source, timeout=25):
    """Run a self-contained test. Returns (outcome, log) where outcome is
    'pass' | 'fail' | 'skip:<reason>'."""
    lang = canon(language)
    entry = LANGS.get(lang)
    if entry is None:
        return "skip:no-executor", f"no table row for {language!r}"
    filename, needs, steps, _image = entry
    if not _has(needs):
        return "skip:no-toolchain", f"{needs} not installed"
    with tempfile.TemporaryDirectory(prefix="evergreen-prove-") as d:
        src = Path(d) / filename
        src.write_text(test_source)
        exe = Path(d) / "prog"
        env = {"PATH": os.environ.get("PATH", ""), "HOME": d, "GOCACHE": d, "GOPATH": d,
               "TMPDIR": d}
        for i, step in enumerate(steps):
            argv = [tok.replace("{F}", str(src)).replace("{X}", str(exe)) for tok in step]
            try:
                r = subprocess.run(argv, cwd=d, env=env, capture_output=True, text=True,
                                   timeout=timeout)
            except subprocess.TimeoutExpired:
                return "skip:timeout", f"step {i} timed out"
            except (FileNotFoundError, PermissionError) as e:
                return "skip:exec-error", str(e)
            is_last = i == len(steps) - 1
            if r.returncode != 0:
                if is_last:
                    return "fail", (r.stdout + r.stderr)[-800:]     # ran, claim broke -> drift
                return "skip:compile-error", (r.stdout + r.stderr)[-800:]  # bad test, not drift
        return "pass", (r.stdout + r.stderr)[-400:]


def selftest():
    langs = available_langs()
    assert "python" in langs, f"python toolchain missing? {langs}"
    ok, _ = run_test("python", "def add(a,b):\n    return a+b\nassert add(2,3)==5\n")
    assert ok == "pass", ok
    bad, _ = run_test("python", "def add(a,b):\n    return a-b\nassert add(2,3)==5\n")
    assert bad == "fail", bad
    comp, _ = run_test("python", "def add(a,b) return a+b\n")  # syntax error -> skip, not fail
    assert comp.startswith("skip"), comp
    missing, _ = run_test("cobol", "whatever")
    assert missing == "skip:no-executor", missing
    print(f"prove selftest ok — executable languages here: {', '.join(langs)}")


if __name__ == "__main__":
    if "--selftest" in sys.argv or "--langs" in sys.argv:
        if "--langs" in sys.argv:
            print(" ".join(available_langs()))
        else:
            selftest()
