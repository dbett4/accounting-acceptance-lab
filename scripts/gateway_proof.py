#!/usr/bin/env python3
from __future__ import annotations

import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aal.control import ActionRequest, BoundedLedger, GenerationConflict
import aal.gateway as gateway_module
from aal.gateway import GatewayStore, GatewayWorker, make_gateway_server
from aal.gateway_load import run_local_load
from aal.gateway_migrations import rollback_gateway, upgrade_gateway
from aal.store import Store


def main() -> None:
    evidence_dir = ROOT / "evidence"
    runtime_dir = ROOT / ".state" / "artifacts"
    evidence_dir.mkdir(exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    controls: dict[str, object] = {}

    with tempfile.TemporaryDirectory(prefix="aal-gateway-proof-") as td:
        root = Path(td)

        http_tokens = {"proof-alpha-token": "tenant-a", "proof-beta-token": "tenant-b", "proof-gamma-token": "tenant-c"}
        server = make_gateway_server("127.0.0.1", 0, root / "http.db", root / "http-artifacts",
                                     http_tokens, max_request_bytes=64, rate_limit=3, rate_window_seconds=60)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        host, port = server.server_address

        def http_request(method: str, path: str, token: str | None = None,
                         body: object | bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict]:
            request_headers = dict(headers or {})
            if token:
                request_headers["Authorization"] = "Bearer " + token
            payload = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
            if body is not None and "Content-Type" not in request_headers:
                request_headers["Content-Type"] = "application/json"
            connection = http.client.HTTPConnection(host, port, timeout=5)
            try:
                connection.request(method, path, body=payload, headers=request_headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        try:
            unauthenticated = http_request("GET", "/v1/jobs")
            created_http = http_request("POST", "/v1/jobs", "proof-alpha-token", {"case_id": "C01"},
                                        {"Idempotency-Key": "http-isolation"})
            http_job_id = created_http[1]["job"]["job_id"]
            foreign_read = http_request("GET", f"/v1/jobs/{http_job_id}", "proof-beta-token")
            foreign_list = http_request("GET", "/v1/jobs", "proof-beta-token")
            foreign_key = http_request("POST", "/v1/jobs", "proof-beta-token", {"case_id": "C02"},
                                       {"Idempotency-Key": "http-isolation"})
            controls["http_authentication_and_tenant_isolation"] = (
                unauthenticated[0] == 401
                and created_http[0] == 202
                and foreign_read[0] == 404
                and foreign_list[0] == 200 and foreign_list[1]["jobs"] == []
                and foreign_key[0] == 202 and foreign_key[1]["job"]["job_id"] != http_job_id
            )

            malformed = http_request("POST", "/v1/jobs", "proof-alpha-token", b"{",
                                     {"Content-Type": "application/json", "Idempotency-Key": "malformed"})
            replay = http_request("POST", "/v1/jobs", "proof-alpha-token", {"case_id": "C01"},
                                  {"Idempotency-Key": "http-isolation"})
            rate_limited = http_request("GET", "/v1/jobs", "proof-alpha-token")
            oversized = http_request("POST", "/v1/jobs", "proof-gamma-token", b"{" + b" " * 65 + b"}",
                                     {"Content-Type": "application/json", "Idempotency-Key": "oversized"})
            controls["strict_request_and_rate_bounds"] = (
                malformed[0] == 400 and malformed[1]["error"]["code"] == "malformed_json"
                and replay[0] == 200
                and rate_limited[0] == 429 and rate_limited[1]["error"]["code"] == "rate_limited"
                and oversized[0] == 413 and oversized[1]["error"]["code"] == "request_too_large"
            )
            server.gateway_store.record_event(
                "redaction_probe", tenant_id="tenant-a", job_id=http_job_id, trace_id="9" * 32,
                span_id="8" * 16, request_id="redaction-proof", state_transition=None,
                duration_ms=None, error_class=None,
                extra={"authorization": "proof-header-secret", "nested": {"token": "proof-nested-secret"}},
            )
            event_text = json.dumps(server.gateway_store.events(), sort_keys=True)
            controls["structured_event_redaction"] = all(
                secret not in event_text for secret in [*http_tokens, "proof-header-secret", "proof-nested-secret"]
            ) and event_text.count("[REDACTED]") >= 2
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join()

        path = root / "gateway.db"
        first = GatewayStore(path)
        job, created = first.submit("tenant-a", "delivery", {"case_id": "C01"}, "a" * 32, "request-a")
        replay, replay_created = first.submit("tenant-a", "delivery", {"case_id": "C01"}, "b" * 32, "request-b")
        controls["duplicate_delivery_one_job"] = created and not replay_created and job["job_id"] == replay["job_id"]
        try:
            first.submit("tenant-a", "delivery", {"case_id": "C02"}, "c" * 32, "request-c")
            conflict = False
        except ValueError:
            conflict = True
        foreign, _ = first.submit("tenant-b", "delivery", {"case_id": "C02"}, "d" * 32, "request-d")
        foreign_hidden = first.get_job("tenant-b", job["job_id"]) is None
        foreign_list, _ = first.list_jobs("tenant-b", 100, None)
        controls["tenant_read_list_idempotency_isolated"] = conflict and foreign_hidden and len(foreign_list) == 1 and foreign["job_id"] != job["job_id"]

        second = GatewayStore(path)
        barrier = threading.Barrier(2)
        outcomes: list[object] = []
        def run_worker(store: GatewayStore, identity: str) -> None:
            barrier.wait()
            outcomes.append(GatewayWorker(store, root / "artifacts").process_one(identity))
        threads = [threading.Thread(target=run_worker, args=(first, "worker-a")),
                   threading.Thread(target=run_worker, args=(second, "worker-b"))]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        controls["concurrent_workers_single_execution"] = sum(value is not None for value in outcomes) == 2 and sum(
            isinstance(value, dict) and value.get("job_id") == job["job_id"] for value in outcomes
        ) == 1

        lease_job = first.submit("tenant-a", "expired", {"case_id": "C02"}, "e" * 32, "request-e")[0]
        abandoned = first.claim("abandoned", 1, now=100)
        before_expiry = first.claim("early", 1, now=100.5)
        recovered = first.claim("recovery", 1, now=101.1)
        controls["expired_claim_recovered"] = abandoned and abandoned["job_id"] == lease_job["job_id"] and before_expiry is None and recovered and recovered["attempts"] == 2
        first.complete(recovered["job_id"], recovered["claim_token"], "failed", None, None, "synthetic_abandoned_claim")

        renewal_path = root / "renewal.db"
        renewal_first = GatewayStore(renewal_path)
        renewal_second = GatewayStore(renewal_path)
        renewal_job = renewal_first.submit("renewal-tenant", "long-running", {"case_id": "C12"},
                                           "7" * 32, "request-renewal")[0]
        renewal_entered = threading.Event()
        renewal_release = threading.Event()
        renewal_calls = 0
        renewal_lock = threading.Lock()
        renewal_outcomes: list[object] = []
        renewal_errors: list[Exception] = []
        original_run_demo = gateway_module.Lab.run_demo

        def blocking_run_demo(lab, reset=False):
            nonlocal renewal_calls
            with renewal_lock:
                renewal_calls += 1
                call_number = renewal_calls
            if call_number == 1:
                renewal_entered.set()
                if not renewal_release.wait(5):
                    raise TimeoutError("renewal proof release timed out")
            return original_run_demo(lab, reset=reset)

        def process_renewal_job() -> None:
            try:
                renewal_outcomes.append(
                    GatewayWorker(renewal_first, root / "renewal-artifacts").process_one("slow-worker", lease_seconds=1)
                )
            except Exception as exc:
                renewal_errors.append(exc)

        with mock.patch.object(gateway_module.Lab, "run_demo", blocking_run_demo):
            renewal_thread = threading.Thread(target=process_renewal_job)
            renewal_thread.start()
            if not renewal_entered.wait(2):
                raise RuntimeError("renewal proof worker did not enter processing")
            original_expiry = renewal_first.get_job("renewal-tenant", renewal_job["job_id"])["lease_expires_at"]
            while time.time() <= original_expiry + 0.1:
                time.sleep(0.02)
            renewal_contender = GatewayWorker(renewal_second, root / "renewal-artifacts").process_one(
                "contender", lease_seconds=1
            )
            renewal_release.set()
            renewal_thread.join(5)
        renewal_final = renewal_first.get_job("renewal-tenant", renewal_job["job_id"])
        renewal_effect_count = 0
        for candidate in (root / "gateway-tenants").glob("*.db"):
            db = sqlite3.connect(candidate)
            try:
                renewal_effect_count += db.execute("SELECT COUNT(*) FROM effects WHERE case_id='C12'").fetchone()[0]
            finally:
                db.close()
        controls["long_running_claim_renewal_single_effect"] = (
            not renewal_thread.is_alive()
            and not renewal_errors
            and renewal_contender is None
            and renewal_calls == 1
            and len(renewal_outcomes) == 1
            and isinstance(renewal_outcomes[0], dict)
            and renewal_outcomes[0].get("state") == "succeeded"
            and renewal_final["state"] == "succeeded"
            and renewal_final["attempts"] == 1
            and renewal_effect_count == 1
        )
        renewal_first.close()
        renewal_second.close()

        unknown = first.submit("tenant-a", "unknown-effect", {"case_id": "C12"}, "f" * 32, "request-f")[0]
        result = GatewayWorker(first, root / "artifacts").process_one("effect-worker")
        # Select tenant-a's isolated database by its already-processed C01 fixture.
        effect_count = 0
        for candidate in (root / "gateway-tenants").glob("*.db"):
            db = sqlite3.connect(candidate)
            try:
                if db.execute("SELECT COUNT(*) FROM cases WHERE case_id='C01'").fetchone()[0]:
                    effect_count = db.execute("SELECT COUNT(*) FROM effects WHERE case_id='C12'").fetchone()[0]
                    break
            finally:
                db.close()
        controls["unknown_effect_read_before_retry"] = result and result["job_id"] == unknown["job_id"] and result["state"] == "succeeded" and effect_count == 1

        timeout = first.submit("tenant-a", "timeout", {"case_id": "C03", "inject_fault": "proposal_timeout"}, "1" * 32, "request-t")[0]
        timeout_worker = GatewayWorker(first, root / "artifacts", max_attempts=2)
        timeout_worker.process_one("timeout-worker")
        first.make_retry_due(timeout["job_id"])
        timeout_worker.process_one("timeout-worker")
        timeout_readback = first.get_job("tenant-a", timeout["job_id"])
        controls["proposal_timeout_bounded_retry"] = timeout_readback["state"] == "failed" and timeout_readback["attempts"] == 2 and timeout_readback["error_class"] == "proposal_timeout"

        first.close()
        reopened = GatewayStore(path)
        controls["restart_persistence"] = reopened.get_job("tenant-a", job["job_id"])["state"] == "succeeded" and reopened.get_job("tenant-a", timeout["job_id"])["state"] == "failed"
        summary = reopened.operational_summary()
        reopened.close()
        controls["database_interruption_readiness_failure"] = not reopened.health_probe()
        second.close()

        control_db = root / "control.db"
        lab_store = Store(control_db)
        lab_store.upsert_case("stale", "timing_difference", 5, "system_authorized", 2)
        try:
            BoundedLedger(lab_store).apply(ActionRequest("stale", "record_timing_adjustment", "5.00", "USD", "stale-key", 1, "deterministic_policy"))
            stale_blocked = False
        except GenerationConflict:
            stale_blocked = True
        lab_store.close()
        controls["stale_generation_blocked"] = stale_blocked

        migration_path = root / "migration.db"
        migration = sqlite3.connect(migration_path)
        migration.execute("CREATE TABLE cases(case_id TEXT PRIMARY KEY)")
        migration.execute("INSERT INTO cases VALUES('preserved')")
        migration.commit()
        upgrade_gateway(migration)
        upgraded = migration.execute("SELECT version FROM gateway_schema_version").fetchone()[0] == 1
        rollback_gateway(migration)
        tables = {row[0] for row in migration.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        preserved = migration.execute("SELECT case_id FROM cases").fetchone()[0] == "preserved"
        migration.close()
        controls["schema_upgrade_rollback_native_readback"] = upgraded and "gateway_jobs" not in tables and preserved

        load = run_local_load(root / "load", requests=100, concurrency=8)
        (runtime_dir / "gateway_load_runtime.json").write_text(json.dumps(load, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        controls["load_executed_without_errors"] = load["completed"] == 100 and load["errors"] == 0

    if not all(value is True for value in controls.values()):
        raise SystemExit(f"gateway proof failed: {controls}")
    canonical = {
        "candidate": "acceptance_gateway",
        "evidence_identity": "deterministic_control_outcomes_only",
        "runtime_timing_artifact": ".state/artifacts/gateway_load_runtime.json",
        "load_workload": load["workload"],
        "controls": controls,
        "operational_summary": summary,
        "limitations": ["synthetic_fixtures", "localhost_only", "credential_free", "not_capacity_or_slo_evidence", "w3c_correlated_structured_spans_not_exported_opentelemetry"],
    }
    output = evidence_dir / "gateway_proof.json"
    output.write_text(json.dumps(canonical, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(canonical, indent=2, sort_keys=True))
    print("GATEWAY_PROOF_PASSED")


if __name__ == "__main__":
    main()
