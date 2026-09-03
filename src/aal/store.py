from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS cases(case_id TEXT PRIMARY KEY, case_type TEXT, amount REAL, state TEXT, generation INTEGER, payload TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS approvals(case_id TEXT PRIMARY KEY, decision TEXT, actor TEXT, reason TEXT, at TEXT);
        CREATE TABLE IF NOT EXISTS effects(effect_id TEXT PRIMARY KEY, case_id TEXT, action TEXT, amount TEXT, currency TEXT, idempotency_key TEXT UNIQUE, created_at TEXT);
        CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, idempotency_key TEXT, request TEXT, status TEXT, effect_id TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS leases(resource TEXT PRIMARY KEY, owner TEXT, expires_at TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at TEXT, event_type TEXT, payload TEXT);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def upsert_case(self, case_id: str, case_type: str, amount: float, state: str, generation: int, payload: dict[str, Any] | None = None):
        self.db.execute("INSERT INTO cases VALUES(?,?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET case_type=excluded.case_type,amount=excluded.amount,state=excluded.state,generation=excluded.generation,payload=excluded.payload",
                        (case_id, case_type, amount, state, generation, json.dumps(payload or {}, sort_keys=True)))
        self.db.commit()

    def case(self, case_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        return dict(row) if row else None

    def cases(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM cases ORDER BY case_id")]

    def approve(self, case_id: str, decision: str, actor: str, reason: str):
        if decision not in {"approved", "rejected"} or not actor.strip():
            raise ValueError("decision and attributable actor required")
        if not self.case(case_id):
            raise KeyError(case_id)
        at = datetime.now(timezone.utc).isoformat()
        self.db.execute("INSERT INTO approvals VALUES(?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET decision=excluded.decision,actor=excluded.actor,reason=excluded.reason,at=excluded.at",
                        (case_id, decision, actor, reason, at))
        self.db.execute("UPDATE cases SET state=?, generation=generation+1 WHERE case_id=?", (decision, case_id))
        self.db.commit()
        self.event("human_decision", {"case_id": case_id, "decision": decision, "actor": actor})

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
        row = self.db.execute("SELECT * FROM leases WHERE resource=?", (resource,)).fetchone()
        if row and datetime.fromisoformat(row["expires_at"]) > now and row["owner"] != owner:
            return False
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        self.db.execute("INSERT INTO leases VALUES(?,?,?) ON CONFLICT(resource) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at", (resource, owner, expires))
        self.db.commit()
        return True

    def event(self, event_type: str, payload: dict[str, Any]):
        sensitive = {"api_key", "token", "secret", "password", "authorization"}
        redacted = {k: ("[REDACTED]" if k.lower() in sensitive else v) for k, v in payload.items()}
        self.db.execute("INSERT INTO events(at,event_type,payload) VALUES(?,?,?)",
                        (datetime.now(timezone.utc).isoformat(), event_type, json.dumps(redacted, sort_keys=True)))
        self.db.commit()

    def events(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY id")]
