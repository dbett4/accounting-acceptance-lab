from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
state = root / ".state"
env = {**os.environ, "PYTHONPATH": str(root / "src")}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def launch(port):
    return subprocess.Popen([sys.executable, "-m", "aal.cli", "--state-dir", str(state), "serve", "--port", str(port)], cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def wait_json(url):
    last = None
    for _ in range(50):
        try:
            with urllib.request.urlopen(url, timeout=.5) as response:
                return json.load(response)
        except Exception as exc:
            last = exc; time.sleep(.1)
    raise RuntimeError(f"service unavailable: {last}")


phases = []
for phase in (1, 2):
    port = free_port(); process = launch(port)
    try:
        health = wait_json(f"http://127.0.0.1:{port}/health")
        case = wait_json(f"http://127.0.0.1:{port}/api/cases/C01")
        phases.append({"phase": phase, "health": health["status"], "persisted_case": case["case_id"], "state": case["state"]})
    finally:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
receipt = {"process_restarts": 1, "phases": phases, "passed": len(phases) == 2 and all(p["health"] == "ok" and p["persisted_case"] == "C01" for p in phases)}
out = state / "artifacts" / "restart_receipt.json"; out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(receipt, indent=2, sort_keys=True))
if not receipt["passed"]: raise SystemExit(1)
