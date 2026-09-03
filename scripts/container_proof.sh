#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .state/artifacts
engine=(docker)
if ! docker info >/dev/null 2>&1; then
  if sudo -n docker info >/dev/null 2>&1; then engine=(sudo -n docker); else
    printf 'Docker daemon unavailable to current user and passwordless sudo\n' >&2
    exit 2
  fi
fi
image=accounting-acceptance-lab:local-proof
container=aal-local-proof
volume=aal-local-proof-state
port=${AAL_CONTAINER_PROOF_PORT:-18080}
"${engine[@]}" rm -f "$container" >/dev/null 2>&1 || true
"${engine[@]}" volume rm "$volume" >/dev/null 2>&1 || true
cleanup() {
  "${engine[@]}" rm -f "$container" >/dev/null 2>&1 || true
  "${engine[@]}" volume rm "$volume" >/dev/null 2>&1 || true
  "${engine[@]}" image rm "$image" >/dev/null 2>&1 || true
}
trap cleanup EXIT
"${engine[@]}" build -t "$image" .
"${engine[@]}" volume create "$volume" >/dev/null
"${engine[@]}" run -d --name "$container" -p "127.0.0.1:${port}:8080" -v "$volume:/state" "$image" >/dev/null
wait_for_health() {
  local value
  for _ in $(seq 1 30); do
    if value=$(python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${port}/health', timeout=1).read().decode())" 2>/dev/null); then
      printf '%s' "$value"
      return 0
    fi
    sleep 1
  done
  return 1
}
health_before=$(wait_for_health)
"${engine[@]}" exec "$container" python -m aal.cli demo >/dev/null
case_before=$(python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${port}/api/cases/C01', timeout=1).read().decode())")
"${engine[@]}" restart "$container" >/dev/null
health_after=$(wait_for_health)
case_after=$(python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:${port}/api/cases/C01', timeout=1).read().decode())")
container_health=starting
for _ in $(seq 1 30); do
  container_health=$("${engine[@]}" inspect --format '{{.State.Health.Status}}' "$container")
  [[ "$container_health" == healthy ]] && break
  sleep 1
done
python3 - "$health_before" "$health_after" "$case_before" "$case_after" "$container_health" <<'PY'
import json
import sys
from pathlib import Path
before_health, after_health, before_case, after_case, container_health = sys.argv[1:]
receipt = {
    "build": "passed",
    "container_health_status": container_health,
    "health_before_restart": json.loads(before_health),
    "health_after_restart": json.loads(after_health),
    "case_before_restart": json.loads(before_case),
    "case_after_restart": json.loads(after_case),
}
receipt["passed"] = (
    receipt["container_health_status"] == "healthy"
    and receipt["health_before_restart"]["status"] == "ok"
    and receipt["health_after_restart"]["status"] == "ok"
    and receipt["case_before_restart"]["case_id"] == "C01"
    and receipt["case_after_restart"]["case_id"] == "C01"
    and receipt["case_after_restart"]["state"] == "accepted"
)
Path(".state/artifacts/container_receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
print(json.dumps(receipt, indent=2, sort_keys=True))
raise SystemExit(0 if receipt["passed"] else 1)
PY
