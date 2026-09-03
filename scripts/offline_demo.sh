#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
python3 -m aal.cli demo
printf '\nArtifacts: %s/.state/artifacts\n' "$PWD"
