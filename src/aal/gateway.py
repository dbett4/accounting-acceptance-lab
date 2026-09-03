from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .app import Lab
from .gateway_migrations import upgrade_gateway

TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
ALLOWED_REQUEST_FIELDS = {"case_id", "inject_fault"}
ALLOWED_FAULTS = {"proposal_timeout"}
SENSITIVE_KEYS = {"api_key", "token", "secret", "password", "authorization", "cookie", "credential"}
SENSITIVE_COMPACT_KEYS = {
    "apikey", "accesstoken", "clientsecret", "passwordhash", "refreshtoken",
    "privatekey", "signingkey", "sessiontoken", "authtoken", "bearertoken", "clientcredential",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _tenant_safe(tenant_id: str) -> str:
    return _digest("tenant:" + tenant_id)[:16]


def _is_sensitive_key(key: str) -> bool:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    normalized = re.sub(r"[^a-z0-9]+", "_", separated.lower()).strip("_")
    parts = set(normalized.split("_"))
    compact = normalized.replace("_", "")
    return normalized in SENSITIVE_KEYS or compact in SENSITIVE_COMPACT_KEYS or bool(parts & SENSITIVE_KEYS)


def _deep_redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): ("[REDACTED]" if _is_sensitive_key(str(key)) else _deep_redact(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_redact(item) for item in value]
    return value


def _new_trace_id() -> str:
    value = secrets.token_hex(16)
    return value if value != "0" * 32 else "1" + value[1:]


def _new_span_id() -> str:
    value = secrets.token_hex(8)
    return value if value != "0" * 16 else "1" + value[1:]


def load_token_bindings(encoded: str | None) -> dict[str, str]:
    """Parse runtime configuration; callers should pass the environment value."""
    if not encoded:
        raise ValueError("AAL_GATEWAY_TOKEN_BINDINGS is required")
    try:
        value = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ValueError("token bindings must be valid JSON") from exc
    if (not isinstance(value, dict) or not value
            or any(not isinstance(token, str) or not token or not isinstance(tenant, str) or not tenant
                   for token, tenant in value.items())):
        raise ValueError("token bindings must be a non-empty string-to-string object")
    return value


class Authenticator:
    """Runtime-only bearer bindings represented internally by SHA-256 digests."""

    def __init__(self, token_to_tenant: dict[str, str]):
        if not token_to_tenant or any(not token or not tenant for token, tenant in token_to_tenant.items()):
            raise ValueError("at least one non-empty token-to-tenant binding is required")
        self._bindings = {_digest(token): tenant for token, tenant in token_to_tenant.items()}

    def authenticate(self, authorization: str | None) -> str | None:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        token = authorization[7:]
        if not token or " " in token:
            return None
        candidate = _digest(token)
        matched = None
        for expected, tenant in self._bindings.items():
            if hmac.compare_digest(candidate, expected):
                matched = tenant
        return matched

    def __repr__(self) -> str:
        return f"Authenticator(bindings={len(self._bindings)})"


class GatewayStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute("PRAGMA journal_mode=WAL")
        upgrade_gateway(self.db)
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def health_probe(self) -> bool:
        try:
            with self._lock:
                return self.db.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        value = dict(row)
        if "request_json" in value:
            value["request"] = json.loads(value.pop("request_json"))
        return value

    def submit(self, tenant_id: str, idempotency_key: str, request: dict[str, Any], trace_id: str,
               request_id: str, now: float | None = None) -> tuple[dict[str, Any], bool]:
        encoded = _canonical(request)
        request_digest = _digest(encoded)
        timestamp = time.time() if now is None else now
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                existing = self.db.execute(
                    "SELECT * FROM gateway_jobs WHERE tenant_id=? AND idempotency_key=?",
                    (tenant_id, idempotency_key),
                ).fetchone()
                if existing:
                    if not hmac.compare_digest(existing["request_digest"], request_digest):
                        raise ValueError("idempotency_key_reused")
                    self.db.commit()
                    return self._row(existing), False
                job_id = "job_" + uuid.uuid4().hex
                self.db.execute(
                    "INSERT INTO gateway_jobs(job_id,tenant_id,idempotency_key,request_digest,request_json,state,trace_id,request_id,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,'queued',?,?,?,?)",
                    (job_id, tenant_id, idempotency_key, request_digest, encoded, trace_id, request_id, timestamp, timestamp),
                )
                row = self.db.execute("SELECT * FROM gateway_jobs WHERE job_id=?", (job_id,)).fetchone()
                self.db.commit()
                return self._row(row), True
            except Exception:
                self.db.rollback()
                raise

    def get_job(self, tenant_id: str, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self.db.execute(
                "SELECT * FROM gateway_jobs WHERE tenant_id=? AND job_id=?", (tenant_id, job_id)
            ).fetchone())

    def list_jobs(self, tenant_id: str, limit: int, cursor: str | None) -> tuple[list[dict[str, Any]], str | None]:
        if limit < 1 or limit > 100:
            raise ValueError("invalid_limit")
        before = 2**63 - 1
        if cursor:
            try:
                before = int(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode())
            except (ValueError, UnicodeDecodeError):
                raise ValueError("invalid_cursor") from None
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM gateway_jobs WHERE tenant_id=? AND seq<? ORDER BY seq DESC LIMIT ?",
                (tenant_id, before, limit + 1),
            ).fetchall()
        more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if more:
            next_cursor = base64.urlsafe_b64encode(str(page[-1]["seq"]).encode()).decode().rstrip("=")
        return [self._row(row) for row in page], next_cursor

    def claim(self, worker_id: str, lease_seconds: int = 30, now: float | None = None,
              max_attempts: int = 3) -> dict[str, Any] | None:
        if lease_seconds < 1 or lease_seconds > 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        if max_attempts < 1 or max_attempts > 10:
            raise ValueError("max_attempts must be between 1 and 10")
        timestamp = time.time() if now is None else now
        token = secrets.token_hex(24)
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute(
                    "UPDATE gateway_jobs SET state='failed',claim_owner=NULL,claim_token=NULL,lease_expires_at=NULL,"
                    "retry_at=NULL,error_class='claim_attempts_exhausted',updated_at=? WHERE attempts>=? AND "
                    "((state='running' AND lease_expires_at<=?) OR (state='retry_wait' AND retry_at<=?))",
                    (timestamp, max_attempts, timestamp, timestamp),
                )
                row = self.db.execute(
                    "SELECT * FROM gateway_jobs WHERE attempts<? AND (state='queued' "
                    "OR (state='retry_wait' AND retry_at<=?) "
                    "OR (state='running' AND lease_expires_at<=?)) ORDER BY seq LIMIT 1",
                    (max_attempts, timestamp, timestamp),
                ).fetchone()
                if not row:
                    self.db.commit()
                    return None
                updated = self.db.execute(
                    "UPDATE gateway_jobs SET state='running',attempts=attempts+1,claim_owner=?,claim_token=?,"
                    "lease_expires_at=?,retry_at=NULL,updated_at=? WHERE job_id=? AND updated_at=?",
                    (worker_id, token, timestamp + lease_seconds, timestamp, row["job_id"], row["updated_at"]),
                )
                if updated.rowcount != 1:
                    self.db.rollback()
                    return None
                claimed = self.db.execute("SELECT * FROM gateway_jobs WHERE job_id=?", (row["job_id"],)).fetchone()
                self.db.commit()
                return self._row(claimed)
            except Exception:
                self.db.rollback()
                raise

    def renew_claim(self, job_id: str, claim_token: str, lease_seconds: int = 30,
                    now: float | None = None) -> bool:
        """Extend an unexpired current claim without reviving a stale lease."""
        if lease_seconds < 1 or lease_seconds > 300:
            raise ValueError("lease_seconds must be between 1 and 300")
        timestamp = time.time() if now is None else now
        with self._lock, self.db:
            updated = self.db.execute(
                "UPDATE gateway_jobs SET lease_expires_at=?,updated_at=? "
                "WHERE job_id=? AND state='running' AND claim_token=? AND lease_expires_at>?",
                (timestamp + lease_seconds, timestamp, job_id, claim_token, timestamp),
            )
        return updated.rowcount == 1

    def complete(self, job_id: str, claim_token: str, state: str, acceptance_id: str | None,
                 effect_id: str | None, error_class: str | None = None, retry_delay: float = 0,
                 now: float | None = None) -> bool:
        if state not in {"succeeded", "failed", "retry_wait"}:
            raise ValueError("illegal terminal transition")
        timestamp = time.time() if now is None else now
        retry_at = timestamp + max(0, retry_delay) if state == "retry_wait" else None
        with self._lock, self.db:
            updated = self.db.execute(
                "UPDATE gateway_jobs SET state=?,acceptance_id=?,effect_id=?,error_class=?,retry_at=?,"
                "claim_owner=NULL,claim_token=NULL,lease_expires_at=NULL,updated_at=? "
                "WHERE job_id=? AND state='running' AND claim_token=? AND lease_expires_at>?",
                (state, acceptance_id, effect_id, error_class, retry_at, timestamp, job_id, claim_token, timestamp),
            )
        return updated.rowcount == 1

    def make_retry_due(self, job_id: str) -> None:
        with self._lock, self.db:
            self.db.execute("UPDATE gateway_jobs SET retry_at=0 WHERE job_id=? AND state='retry_wait'", (job_id,))

    def record_event(self, event_type: str, *, tenant_id: str | None, job_id: str | None,
                     trace_id: str, span_id: str, request_id: str, state_transition: str | None,
                     duration_ms: float | None, error_class: str | None, extra: dict[str, Any] | None = None) -> None:
        safe = _tenant_safe(tenant_id) if tenant_id else None
        encoded = _canonical(_deep_redact(extra or {}))
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO gateway_events(event_type,tenant_safe_id,job_id,trace_id,span_id,request_id,state_transition,duration_ms,error_class,extra_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (event_type, safe, job_id, trace_id, span_id, request_id, state_transition, duration_ms, error_class, encoded),
            )

    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM gateway_events ORDER BY id").fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["extra"] = json.loads(value.pop("extra_json"))
            values.append(value)
        return values

    def operational_summary(self) -> dict[str, Any]:
        with self._lock:
            states = {row["state"]: row["count"] for row in self.db.execute(
                "SELECT state,COUNT(*) AS count FROM gateway_jobs GROUP BY state ORDER BY state"
            )}
            event_counts = {row["event_type"]: row["count"] for row in self.db.execute(
                "SELECT event_type,COUNT(*) AS count FROM gateway_events GROUP BY event_type ORDER BY event_type"
            )}
            attempts = self.db.execute("SELECT COALESCE(SUM(attempts),0) FROM gateway_jobs").fetchone()[0]
        return {"schema_version": 1, "job_states": states, "total_attempts": attempts, "event_counts": event_counts}


class ClaimLostError(RuntimeError):
    """The worker no longer owns the job and must not mutate its state."""


class _ClaimHeartbeat:
    def __init__(self, store: GatewayStore, job_id: str, claim_token: str, lease_seconds: int):
        self.store = store
        self.job_id = job_id
        self.claim_token = claim_token
        self.lease_seconds = lease_seconds
        self.interval = min(1.0, lease_seconds / 3)
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._error: Exception | None = None
        self._thread = threading.Thread(target=self._run, name=f"gateway-lease-{job_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                renewed = self.store.renew_claim(self.job_id, self.claim_token, self.lease_seconds)
            except Exception as exc:
                self._error = exc
                self._lost.set()
                return
            if not renewed:
                self._lost.set()
                return

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def stop_and_fence(self) -> None:
        self.stop()
        if self._lost.is_set():
            raise ClaimLostError("claim_lost") from self._error
        try:
            renewed = self.store.renew_claim(self.job_id, self.claim_token, self.lease_seconds)
        except Exception as exc:
            raise ClaimLostError("claim_lost") from exc
        if not renewed:
            raise ClaimLostError("claim_lost")


class GatewayWorker:
    def __init__(self, store: GatewayStore, artifact_dir: str | Path, max_attempts: int = 3):
        if max_attempts < 1 or max_attempts > 10:
            raise ValueError("max_attempts must be between 1 and 10")
        self.store = store
        self.artifact_dir = Path(artifact_dir)
        self.max_attempts = max_attempts

    def process_one(self, worker_id: str, lease_seconds: int = 30) -> dict[str, Any] | None:
        started = time.perf_counter()
        job = self.store.claim(worker_id, lease_seconds, max_attempts=self.max_attempts)
        if not job:
            return None
        span_id = _new_span_id()
        heartbeat = _ClaimHeartbeat(self.store, job["job_id"], job["claim_token"], lease_seconds)
        heartbeat.start()
        acceptance_id = None
        effect_id = None
        error_class = None
        try:
            request = job["request"]
            if request.get("inject_fault") == "proposal_timeout":
                raise TimeoutError("synthetic proposal timeout")
            tenant_safe = _tenant_safe(job["tenant_id"])
            tenant_db = self.store.path.parent / "gateway-tenants" / f"{tenant_safe}.db"
            lab = Lab(tenant_db, self.artifact_dir / tenant_safe / job["job_id"])
            try:
                fixture = next((copy.deepcopy(item) for item in lab.fixtures if item["case_id"] == request["case_id"]), None)
                if not fixture:
                    raise ValueError("unknown_case")
                lab.fixtures = [fixture]
                lab.run_demo(reset=False)
                effects = lab.store.effects_for_case(request["case_id"])
                effect_id = effects[0]["effect_id"] if effects else None
                acceptance_id = request["case_id"]
            finally:
                lab.close()
            state = "succeeded"
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                error_class = "proposal_timeout"
            elif isinstance(exc, ValueError) and str(exc) == "unknown_case":
                error_class = "invalid_request"
            else:
                error_class = "processing_error"
            state = "failed" if job["attempts"] >= self.max_attempts or error_class == "invalid_request" else "retry_wait"
            acceptance_id = None
            effect_id = None
        except BaseException:
            heartbeat.stop()
            raise

        try:
            heartbeat.stop_and_fence()
        except ClaimLostError:
            self.store.record_event("job_attempt", tenant_id=job["tenant_id"], job_id=job["job_id"],
                                    trace_id=job["trace_id"], span_id=span_id, request_id=job["request_id"],
                                    state_transition="running->claim_lost", duration_ms=round((time.perf_counter() - started) * 1000, 3),
                                    error_class="claim_lost", extra={"attempt": job["attempts"]})
            raise
        if not self.store.complete(job["job_id"], job["claim_token"], state, acceptance_id, effect_id,
                                   error_class, retry_delay=1):
            raise ClaimLostError("claim_lost")
        duration = round((time.perf_counter() - started) * 1000, 3)
        self.store.record_event("job_attempt", tenant_id=job["tenant_id"], job_id=job["job_id"],
                                trace_id=job["trace_id"], span_id=span_id, request_id=job["request_id"],
                                state_transition=f"running->{state}", duration_ms=duration,
                                error_class=error_class, extra={"attempt": job["attempts"]})
        result = self.store.get_job(job["tenant_id"], job["job_id"])
        return {"job_id": job["job_id"], "state": result["state"], "error_class": result["error_class"]}


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float):
        if limit < 1 or window_seconds <= 0:
            raise ValueError("invalid rate limit")
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, tenant_id: str, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[tenant_id]
            while hits and hits[0] <= timestamp - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(timestamp)
            return True


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    allowed = {"job_id", "state", "attempts", "error_class", "acceptance_id", "effect_id", "request", "created_at", "updated_at"}
    return {key: job[key] for key in allowed if key in job}


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_close(self) -> None:
        try:
            self.gateway_store.close()
        except sqlite3.Error:
            pass
        super().server_close()


def make_gateway_server(host: str, port: int, db_path: str | Path, artifact_dir: str | Path,
                        token_to_tenant: dict[str, str], *, max_request_bytes: int = 16_384,
                        rate_limit: int = 60, rate_window_seconds: float = 60) -> GatewayServer:
    store = GatewayStore(db_path)
    authenticator = Authenticator(token_to_tenant)
    limiter = SlidingWindowRateLimiter(rate_limit, rate_window_seconds)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AcceptanceGateway/1"
        sys_version = ""

        def _context(self) -> tuple[str, str, str] | None:
            incoming = self.headers.get("traceparent")
            if incoming:
                match = TRACEPARENT.fullmatch(incoming)
                if not match or match.group(1) == "0" * 32 or match.group(2) == "0" * 16:
                    self._error(400, "invalid_traceparent", "traceparent is invalid")
                    return None
                trace_id = match.group(1)
            else:
                trace_id = _new_trace_id()
            request_id = self.headers.get("X-Request-ID") or ("req_" + uuid.uuid4().hex)
            if not REQUEST_ID.fullmatch(request_id):
                self._request_id = "req_" + uuid.uuid4().hex
                self._trace_id = trace_id
                self._span_id = _new_span_id()
                self._error(400, "invalid_request_id", "request ID is invalid")
                return None
            self._request_id, self._trace_id, self._span_id = request_id, trace_id, _new_span_id()
            self._persisted_request_id = (
                "rid_" + _digest("request:" + request_id)[:24]
                if self.headers.get("X-Request-ID") else request_id
            )
            return request_id, trace_id, self._span_id

        def _headers(self, length: int) -> None:
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if hasattr(self, "_request_id"):
                self.send_header("X-Request-ID", self._request_id)
                self.send_header("traceparent", f"00-{self._trace_id}-{self._span_id}-01")

        def _json(self, status: int, value: Any) -> None:
            body = (_canonical(value) + "\n").encode()
            self.send_response(status)
            self._headers(len(body))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, code: str, message: str) -> None:
            request_id = getattr(self, "_request_id", "unavailable")
            self._json(status, {"error": {"code": code, "message": message}, "request_id": request_id})

        def _authenticate(self) -> str | None:
            tenant = authenticator.authenticate(self.headers.get("Authorization"))
            if not tenant:
                self._error(401, "unauthorized", "valid bearer authentication is required")
                return None
            if not limiter.allow(tenant):
                self._error(429, "rate_limited", "tenant request rate exceeded")
                return None
            return tenant

        def _read_request(self) -> dict[str, Any] | None:
            if self.headers.get("Content-Type") != "application/json":
                self._error(415, "unsupported_media_type", "Content-Type must be application/json")
                return None
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                length = -1
            if length < 0:
                self._error(411, "length_required", "a valid Content-Length is required")
                return None
            if length > max_request_bytes:
                self._error(413, "request_too_large", "request exceeds the byte limit")
                return None
            try:
                value = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._error(400, "malformed_json", "request body is not valid JSON")
                return None
            if not isinstance(value, dict):
                self._error(400, "invalid_request", "request body must be an object")
                return None
            return value

        def _begin(self) -> tuple[str | None, float]:
            started = time.perf_counter()
            if self._context() is None:
                return None, started
            return self._authenticate(), started

        def _record(self, tenant: str | None, started: float, status: int, job_id: str | None = None,
                    transition: str | None = None, error_class: str | None = None) -> None:
            if not tenant or not store.health_probe():
                return
            store.record_event("http_request", tenant_id=tenant, job_id=job_id, trace_id=self._trace_id,
                               span_id=self._span_id, request_id=self._persisted_request_id, state_transition=transition,
                               duration_ms=round((time.perf_counter() - started) * 1000, 3),
                               error_class=error_class, extra={"method": self.command, "status": status})

        def do_POST(self) -> None:
            tenant, started = self._begin()
            if not tenant:
                return
            path = urlparse(self.path).path
            if path != "/v1/jobs":
                self._error(404, "not_found", "resource not found"); self._record(tenant, started, 404); return
            body = self._read_request()
            if body is None:
                self._record(tenant, started, 400, error_class="invalid_request"); return
            unknown = set(body) - ALLOWED_REQUEST_FIELDS
            case_id = body.get("case_id")
            fault = body.get("inject_fault")
            key = self.headers.get("Idempotency-Key", "")
            if unknown or not isinstance(case_id, str) or not re.fullmatch(r"C(?:0[1-9]|1[0-2])", case_id) or (fault is not None and fault not in ALLOWED_FAULTS):
                self._error(400, "invalid_request", "allowed fields are case_id and inject_fault"); self._record(tenant, started, 400, error_class="invalid_request"); return
            if not IDEMPOTENCY_KEY.fullmatch(key):
                self._error(400, "invalid_idempotency_key", "a valid Idempotency-Key is required"); self._record(tenant, started, 400, error_class="invalid_request"); return
            try:
                job, created = store.submit(tenant, key, body, self._trace_id, self._persisted_request_id)
            except ValueError:
                self._error(409, "idempotency_conflict", "idempotency key was used for another request")
                self._record(tenant, started, 409, error_class="idempotency_conflict"); return
            status = 202 if created else 200
            self._json(status, {"job": _public_job(job), "replayed": not created, "request_id": self._request_id})
            self._record(tenant, started, status, job["job_id"], "submitted->queued" if created else "idempotent_replay")

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"/health", "/ready"}:
                self._request_id, self._trace_id, self._span_id = "health", _new_trace_id(), _new_span_id()
                healthy = store.health_probe()
                self._json(200 if healthy else 503, {"status": "ok" if healthy else "error", "database": "reachable" if healthy else "unreachable"})
                return
            tenant, started = self._begin()
            if not tenant:
                return
            if parsed.path in {"/v1/health", "/v1/ready"}:
                healthy = store.health_probe(); status = 200 if healthy else 503
                self._json(status, {"status": "ok" if healthy else "error", "database": "reachable" if healthy else "unreachable"})
                self._record(tenant, started, status); return
            if parsed.path == "/v1/jobs":
                query = parse_qs(parsed.query, keep_blank_values=True)
                if set(query) - {"limit", "cursor"} or any(len(values) != 1 for values in query.values()):
                    self._error(400, "invalid_query", "only one limit and cursor are allowed"); self._record(tenant, started, 400); return
                try:
                    limit = int(query.get("limit", ["50"])[0])
                    jobs, cursor = store.list_jobs(tenant, limit, query.get("cursor", [None])[0])
                except ValueError:
                    self._error(400, "invalid_query", "limit or cursor is invalid"); self._record(tenant, started, 400); return
                self._json(200, {"jobs": [_public_job(job) for job in jobs], "next_cursor": cursor, "request_id": self._request_id})
                self._record(tenant, started, 200); return
            prefix = "/v1/jobs/"
            if parsed.path.startswith(prefix) and "/" not in parsed.path[len(prefix):] and parsed.path[len(prefix):]:
                job = store.get_job(tenant, parsed.path[len(prefix):])
                if not job:
                    self._error(404, "not_found", "resource not found"); self._record(tenant, started, 404); return
                self._json(200, {"job": _public_job(job), "request_id": self._request_id})
                self._record(tenant, started, 200, job["job_id"]); return
            self._error(404, "not_found", "resource not found"); self._record(tenant, started, 404)

        def _unsupported_method(self) -> None:
            path = urlparse(self.path).path
            if path == "/v1" or path.startswith("/v1/"):
                tenant, started = self._begin()
                if not tenant:
                    return
                self._error(405, "method_not_allowed", "method is not allowed for this resource")
                self._record(tenant, started, 405, error_class="method_not_allowed")
                return
            self._request_id, self._trace_id, self._span_id = "unavailable", _new_trace_id(), _new_span_id()
            self._error(404, "not_found", "resource not found")

        do_PUT = _unsupported_method
        do_PATCH = _unsupported_method
        do_DELETE = _unsupported_method
        do_OPTIONS = _unsupported_method

        def __getattr__(self, name: str) -> Any:
            if name.startswith("do_"):
                return self._unsupported_method
            raise AttributeError(name)

        def log_message(self, *_: Any) -> None:
            return

    server = GatewayServer((host, port), Handler)
    server.gateway_store = store
    server.gateway_artifact_dir = Path(artifact_dir)
    return server
