#!/usr/bin/env bash
# Build the real, disposable Git repository used by the cultivate exam.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SOURCE_ROOT="$(git -C "$HERE/../.." rev-parse --show-toplevel)"
TMP="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/cultivate-exam.XXXXXX")" && pwd -P)"

cleanup_on_error() {
  find "$TMP" -depth -delete
}
trap cleanup_on_error ERR

cp -R "$HERE/template/." "$TMP/"
git -C "$TMP" init -q
git -C "$TMP" config user.name "Cultivate Exam"
git -C "$TMP" config user.email "cultivate-exam@example.invalid"

while IFS=$'\t' read -r id path kind tracked expected note; do
  [ "$id" = "id" ] && continue
  if [ "$tracked" = "yes" ]; then
    git -C "$TMP" add -- "$path"
  fi
done < "$HERE/manifest.tsv"
git -C "$TMP" commit -qm "fixture: cultivate inventory"

FIXTURE_ROOT="$(git -C "$TMP" rev-parse --show-toplevel)"
if [ "$FIXTURE_ROOT" != "$TMP" ] || [ "$FIXTURE_ROOT" = "$SOURCE_ROOT" ]; then
  echo "error: cultivate fixture escaped its isolated temporary repository" >&2
  exit 1
fi

trap - ERR
printf '%s\n' "$TMP"
