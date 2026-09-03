from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


class ApprovalError(PermissionError):
    pass


class Store:
    CASE_ACTIONS = {
        "duplicate":"reverse_duplicate",
        "timing_difference":"record_timing_adjustment",
        "retry_after_unknown_external_effect":"record_timing_adjustment",
    }

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA busy_timeout=30000;
        CREATE TABLE IF NOT EXISTS cases(case_id TEXT PRIMARY KEY, case_type TEXT, amount REAL, state TEXT, generation INTEGER, payload TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS approvals(case_id TEXT PRIMARY KEY, decision TEXT, actor TEXT, reason TEXT, at TEXT, proposal_digest TEXT, action TEXT, amount TEXT, currency TEXT, generation INTEGER);
        CREATE TABLE IF NOT EXISTS effects(effect_id TEXT PRIMARY KEY, case_id TEXT, action TEXT, amount TEXT, currency TEXT, idempotency_key TEXT UNIQUE, created_at TEXT, request_digest TEXT, request_json TEXT, expected_generation INTEGER, authority TEXT, proposal_digest TEXT);
        CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, idempotency_key TEXT, request TEXT, status TEXT, effect_id TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS leases(resource TEXT PRIMARY KEY, owner TEXT, expires_at TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at TEXT, event_type TEXT, payload TEXT);
        """)
        self._migrate_columns("approvals", {"proposal_digest":"TEXT", "action":"TEXT", "amount":"TEXT", "currency":"TEXT", "generation":"INTEGER"})
        self._migrate_columns("effects", {"request_digest":"TEXT", "request_json":"TEXT", "expected_generation":"INTEGER", "authority":"TEXT", "proposal_digest":"TEXT"})
        self.db.commit()

    def _migrate_columns(self, table: str, columns: dict[str, str]) -> None:
        present = {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}
        for name, sql_type in columns.items():
            if name not in present:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    def close(self):
        self.db.close()

    def health_probe(self) -> bool:
        try:
            return self.db.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def upsert_case(self, case_id: str, case_type: str, amount: float, state: str, generation: int, payload: dict[str, Any] | None = None):
        encoded = json.dumps(payload or {}, sort_keys=True)
        with self.db:
            current = self.db.execute("SELECT case_type,amount,state,generation,payload FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if current and current["state"] == "rejected" and state != "rejected":
                raise ApprovalError("rejection is terminal; reset must delete the case explicitly")
            identity_changed = current and (
                current["payload"] != encoded or current["case_type"] != case_type
                or Decimal(str(current["amount"])) != Decimal(str(amount))
                or current["generation"] != generation
            )
            if identity_changed:
                self.db.execute("DELETE FROM approvals WHERE case_id=?", (case_id,))
            self.db.execute("INSERT INTO cases VALUES(?,?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET case_type=excluded.case_type,amount=excluded.amount,state=excluded.state,generation=excluded.generation,payload=excluded.payload",
                            (case_id, case_type, amount, state, generation, encoded))

    def case(self, case_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        return dict(row) if row else None

    def cases(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM cases ORDER BY case_id")]

    def approve(self, case_id: str, decision: str, actor: str, reason: str):
        if decision not in {"approved", "rejected"} or not actor.strip():
            raise ValueError("decision and attributable actor required")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            case = self.db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if not case:
                raise KeyError(case_id)
            if case["state"] == "rejected":
                raise ApprovalError("rejection is terminal")
            payload = json.loads(case["payload"])
            proposal = payload.get("proposal")
            scope = payload.get("proposed_action")
            if decision == "approved" and (not isinstance(proposal, dict) or not isinstance(scope, dict)):
                raise ApprovalError("approval requires an exact stored proposal and action scope")
            proposal_hash = None
            action = amount = currency = None
            if decision == "approved":
                proposal_hash = hashlib.sha256(json.dumps(proposal, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
                action = scope.get("action")
                amount = format(Decimal(str(scope.get("amount"))).quantize(Decimal("0.01")), "f")
                currency = scope.get("currency")
                if (not action or not currency or proposal.get("recommendation") != action
                        or self.CASE_ACTIONS.get(case["case_type"]) != action):
                    raise ApprovalError("approval action scope does not match stored proposal")
            generation = int(case["generation"]) + 1
            at = datetime.now(timezone.utc).isoformat()
            updated = self.db.execute("UPDATE cases SET state=?, generation=? WHERE case_id=? AND generation=?", (decision, generation, case_id, case["generation"]))
            if updated.rowcount != 1:
                raise ApprovalError("case generation changed during approval")
            self.db.execute(
                "INSERT INTO approvals(case_id,decision,actor,reason,at,proposal_digest,action,amount,currency,generation) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(case_id) DO UPDATE SET decision=excluded.decision,actor=excluded.actor,reason=excluded.reason,at=excluded.at,proposal_digest=excluded.proposal_digest,action=excluded.action,amount=excluded.amount,currency=excluded.currency,generation=excluded.generation",
                (case_id, decision, actor, reason, at, proposal_hash, action, amount, currency, generation),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.event("human_decision", {"case_id":case_id,"decision":decision,"actor":actor,"proposal_digest":proposal_hash})

    def approval(self, case_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM approvals WHERE case_id=?", (case_id,)).fetchone()
        return dict(row) if row else None

    def effects_for_case(self, case_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM effects WHERE case_id=? ORDER BY created_at", (case_id,))]

    def effect_by_key(self, key: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM effects WHERE idempotency_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def record_attempt(self, key: str, request: dict[str, Any], status: str, effect_id: str | None):
        self.db.execute("INSERT INTO attempts(idempotency_key,request,status,effect_id,created_at) VALUES(?,?,?,?,?)",
                        (key, json.dumps(request, sort_keys=True), status, effect_id, datetime.now(timezone.utc).isoformat()))
        self.db.commit()

    def uncertain_attempt(self, key: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM attempts WHERE idempotency_key=? AND status='effect_unknown' ORDER BY id DESC LIMIT 1", (key,)).fetchone()
        return dict(row) if row else None

    def acquire_lease(self, resource: str, owner: str, ttl_seconds: int) -> bool:
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        with self.db:
            result = self.db.execute(
                "INSERT INTO leases(resource,owner,expires_at) VALUES(?,?,?) ON CONFLICT(resource) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at WHERE leases.expires_at<=? OR leases.owner=excluded.owner",
                (resource, owner, expires, now.isoformat()),
            )
        return result.rowcount == 1

    def event(self, event_type: str, payload: dict[str, Any]):
        sensitive = {"api_key", "token", "secret", "password", "authorization"}
        redacted = {k: ("[REDACTED]" if k.lower() in sensitive else v) for k, v in payload.items()}
        self.db.execute("INSERT INTO events(at,event_type,payload) VALUES(?,?,?)", (datetime.now(timezone.utc).isoformat(), event_type, json.dumps(redacted, sort_keys=True)))
        self.db.commit()

    def events(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY id")]
