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
    required = {"case_id","terminal_verdict","sources","policy_version","citations","proposal","approval","action","readback","verifier","residuals","estimated_cost_usd"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"packet missing fields: {sorted(missing)}")
    # Runtime telemetry is deliberately noncanonical. It may be supplied by older
    # callers but can never affect verdict identity.
    canonical_payload = {key:value for key,value in payload.items() if key not in {"latency_ms","telemetry"}}
    canonical = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
    return AcceptancePacket(canonical, hashlib.sha256(canonical).hexdigest())
