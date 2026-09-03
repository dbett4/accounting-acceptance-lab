from __future__ import annotations

import sqlite3

GATEWAY_SCHEMA_VERSION = 1


def upgrade_gateway(db: sqlite3.Connection) -> None:
    """Upgrade gateway-owned tables without changing lab-owned tables."""
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS gateway_schema_version(
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            version INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gateway_jobs(
            seq INTEGER PRIMARY KEY,
            job_id TEXT NOT NULL UNIQUE,
            tenant_id TEXT NOT NULL,
            idempotency_digest TEXT NOT NULL,
            request_digest TEXT NOT NULL,
            request_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('queued','running','retry_wait','succeeded','failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            claim_owner TEXT,
            claim_token TEXT,
            lease_expires_at REAL,
            retry_at REAL,
            error_class TEXT,
            acceptance_id TEXT,
            effect_id TEXT,
            trace_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(tenant_id, idempotency_digest)
        );
        CREATE INDEX IF NOT EXISTS gateway_jobs_claim
            ON gateway_jobs(state, retry_at, lease_expires_at, seq);
        CREATE INDEX IF NOT EXISTS gateway_jobs_tenant_page
            ON gateway_jobs(tenant_id, seq DESC);
        CREATE TABLE IF NOT EXISTS gateway_events(
            id INTEGER PRIMARY KEY,
            event_type TEXT NOT NULL,
            tenant_safe_id TEXT,
            job_id TEXT,
            trace_id TEXT NOT NULL,
            span_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            state_transition TEXT,
            duration_ms REAL,
            error_class TEXT,
            extra_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    current = db.execute("SELECT version FROM gateway_schema_version WHERE singleton=1").fetchone()
    if current and current[0] > GATEWAY_SCHEMA_VERSION:
        raise RuntimeError("gateway schema is newer than this application")
    db.execute(
        "INSERT INTO gateway_schema_version(singleton,version) VALUES(1,?) "
        "ON CONFLICT(singleton) DO UPDATE SET version=excluded.version",
        (GATEWAY_SCHEMA_VERSION,),
    )
    db.commit()


def rollback_gateway(db: sqlite3.Connection) -> None:
    """Remove only gateway-owned schema; lab data is deliberately preserved."""
    db.executescript(
        """
        DROP INDEX IF EXISTS gateway_jobs_claim;
        DROP INDEX IF EXISTS gateway_jobs_tenant_page;
        DROP TABLE IF EXISTS gateway_events;
        DROP TABLE IF EXISTS gateway_jobs;
        DROP TABLE IF EXISTS gateway_schema_version;
        """
    )
    db.commit()
