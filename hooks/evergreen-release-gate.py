#!/usr/bin/env python3
"""Fail-closed certification gate for local `evergreen--v*` release tags.

A release tag is a certification claim about the commit it names, so creating one requires
proof that the winnow (documentation freshness) and cultivate (repo hygiene) passes actually
walked that commit. Proof is two checked-in receipts — `eval/certifications/winnow.json` and
`eval/certifications/cultivate.json` — read from the HEAD tree, never from the working
directory, so an uncommitted or locally edited record can never vouch for a tag. Each record
names the commit its pass ran at; the winnow record must also report zero drift and zero
unverified claims with counts that sum to its total. The certified commit must be HEAD itself
or an ancestor of HEAD from which nothing outside `eval/certifications/` has since changed:
committing the records is the only history permitted between running a pass and tagging.

The gate verifies record arithmetic and commit binding. That a record honestly reflects a
pass that ran remains the runner's responsibility — a gate cannot re-run the reasoning pass.
Exit 0 = certified. Any refusal exits 2 with the failing check named on stderr.
"""

import argparse
import json
import re
import subprocess
import sys

RECORD_DIR = "eval/certifications"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CLAIM_KEYS = ("total", "certified", "drift", "unverified")


def _git(repo: str, *args: str) -> "tuple[int, str]":
    completed = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True,
    )
    return completed.returncode, completed.stdout


def _refuse(reason: str) -> None:
    print(f"evergreen release gate: {reason}", file=sys.stderr)
    raise SystemExit(2)


def _validate_claims(path: str, record: dict) -> None:
    """The winnow arithmetic: non-negative integer counts, zero findings, counts that sum."""
    claims = record.get("claims")
    if not isinstance(claims, dict) or not all(
        isinstance(claims.get(key), int)
        and not isinstance(claims.get(key), bool)
        and claims.get(key) >= 0
        for key in CLAIM_KEYS
    ):
        _refuse(
            f'{path} must carry non-negative integer "claims" counts: '
            "total, certified, drift, unverified"
        )
    if claims["drift"] or claims["unverified"]:
        _refuse(
            f"winnow record reports drift={claims['drift']} "
            f"unverified={claims['unverified']}; a release tag requires zero of both"
        )
    if claims["certified"] + claims["drift"] + claims["unverified"] != claims["total"]:
        _refuse(f"{path} claim counts do not sum to total")


def _bind_subject(repo: str, path: str, kind: str, subject: str, head: str) -> None:
    """Refuse unless only certification records changed between the certified commit and HEAD."""
    if subject == head:
        return
    rc, _ = _git(repo, "cat-file", "-e", f"{subject}^{{commit}}")
    if rc:
        _refuse(f"{path} certifies {subject}, which is not a commit in this repository")
    rc, _ = _git(repo, "merge-base", "--is-ancestor", subject, head)
    if rc:
        _refuse(f"{path} certifies {subject}, which is not an ancestor of HEAD")
    rc, changed = _git(repo, "diff", "--name-only", subject, head)
    if rc:
        _refuse(f"could not diff certified commit {subject} against HEAD")
    outside = [
        line for line in changed.splitlines()
        if line and not line.startswith(RECORD_DIR + "/")
    ]
    if outside:
        _refuse(
            f"{path} is stale: {outside[0]} changed after certified commit {subject}; "
            f"re-run the {kind} pass at HEAD and recommit the record"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify committed winnow/cultivate certification records at HEAD.",
    )
    parser.add_argument("--repo", required=True, help="repository root to certify")
    repo = parser.parse_args().repo

    rc, head = _git(repo, "rev-parse", "--verify", "HEAD")
    if rc:
        _refuse("the repository has no HEAD commit to certify")
    head = head.strip()

    for kind in ("winnow", "cultivate"):
        path = f"{RECORD_DIR}/{kind}.json"
        rc, blob = _git(repo, "cat-file", "blob", f"HEAD:{path}")
        if rc:
            _refuse(
                f"no {kind} certification record committed at HEAD — run the {kind} pass, "
                f"write {path} naming the commit it ran at, and commit it"
            )
        try:
            record = json.loads(blob)
        except ValueError:
            _refuse(f"{path} at HEAD is not valid JSON")
        if not isinstance(record, dict) or record.get("pass") != kind:
            _refuse(f'{path} must be a JSON object with "pass": "{kind}"')
        subject = record.get("commit")
        if not isinstance(subject, str) or not SHA_RE.match(subject):
            _refuse(f'{path} "commit" must be the full 40-hex SHA the pass ran at')
        date = record.get("date")
        if not isinstance(date, str) or not date:
            _refuse(f'{path} "date" must be a non-empty string')
        if kind == "winnow":
            _validate_claims(path, record)
        _bind_subject(repo, path, kind, subject, head)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
