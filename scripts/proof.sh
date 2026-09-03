#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .state/artifacts
: > .state/artifacts/proof-output.txt
run() {
  { printf '\n$'; printf ' %q' "$@"; printf '\n'; } | tee -a .state/artifacts/proof-output.txt
  "$@" 2>&1 | tee -a .state/artifacts/proof-output.txt
}
run env PYTHONPATH=src python3 -m unittest discover -s tests -v
run env PYTHONPATH=src python3 -m aal.cli demo
run env PYTHONPATH=src python3 -m aal.cli benchmark
run env PYTHONPATH=src python3 scripts/restart_proof.py
run python3 scripts/secret_scan.py
printf '\nALL_LOCAL_PROOF_GATES_PASSED\n' | tee -a .state/artifacts/proof-output.txt
