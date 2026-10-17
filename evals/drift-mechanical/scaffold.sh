#!/usr/bin/env bash
# Materialise eval/fixture (the seeded-drift exam; answer key in eval/manifest.tsv) as the workspace.
set -euo pipefail
cp -R "$(dirname "$0")/../../eval/fixture/." .
rm -rf __pycache__
