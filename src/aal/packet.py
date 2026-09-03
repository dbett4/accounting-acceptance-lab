from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AcceptancePacket:
    canonical_json: bytes
    sha256: str


def compile_packet(payload: dict[str, Any]) -> AcceptancePacket:
    required = {"case_id", "sources", "policy_version", "citations", "proposal", "approval", "action", "readback", "verifier", "residuals", "latency_ms", "estimated_cost_usd"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"packet missing fields: {sorted(missing)}")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
    return AcceptancePacket(canonical, hashlib.sha256(canonical).hexdigest())
