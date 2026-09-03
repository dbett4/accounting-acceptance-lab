import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import aal.gateway as gateway_module
from aal.gateway import Authenticator, GatewayStore, GatewayWorker, load_token_bindings, make_gateway_server
from aal.gateway_load import percentile, run_local_load
from aal.gateway_migrations import rollback_gateway, upgrade_gateway


class GatewayStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "gateway.db"
        self.store = GatewayStore(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_idempotency_is_tenant_scoped_and_payload_bound(self):
        request = {"case_id": "C01"}
        first, created = self.store.submit("tenant-a", "same-key", request, "trace-a", "req-a")
        replay, created_again = self.store.submit("tenant-a", "same-key", request, "trace-b", "req-b")
        foreign, foreign_created = self.store.submit("tenant-b", "same-key", request, "trace-c", "req-c")
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["job_id"], replay["job_id"])
        self.assertTrue(foreign_created)
        self.assertNotEqual(first["job_id"], foreign["job_id"])
        with self.assertRaisesRegex(ValueError, "idempotency_key_reused"):
            self.store.submit("tenant-a", "same-key", {"case_id": "C02"}, "trace", "req")

    def test_owned_read_and_stable_bounded_cursor_list(self):
        ids = [self.store.submit("tenant-a", f"key-{i}", {"case_id": "C01"}, "t", "r")[0]["job_id"] for i in range(4)]
        self.store.submit("tenant-b", "other", {"case_id": "C01"}, "t", "r")
        self.assertIsNone(self.store.get_job("tenant-b", ids[0]))
        page1, cursor = self.store.list_jobs("tenant-a", 2, None)
        page2, end = self.store.list_jobs("tenant-a", 2, cursor)
        self.assertEqual([j["job_id"] for j in page1 + page2], list(reversed(ids)))
        self.assertIsNone(end)
        self.assertTrue(all(j["tenant_id"] == "tenant-a" for j in page1 + page2))
        with self.assertRaises(ValueError):
            self.store.list_jobs("tenant-a", 101, None)

    def test_atomic_claim_expiry_recovery_and_claim_token_completion(self):
        job = self.store.submit("tenant-a", "claim", {"case_id": "C01"}, "t", "r")[0]
        other = GatewayStore(self.path)
        barrier = threading.Barrier(2)
        outcomes = []
        def claim(store, worker):
            barrier.wait()
            value = store.claim(worker, lease_seconds=1, now=100.0)
            outcomes.append(value)
        threads = [threading.Thread(target=claim, args=(self.store, "one")), threading.Thread(target=claim, args=(other, "two"))]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        claimed = [value for value in outcomes if value]
        self.assertEqual(len(claimed), 1)
        self.assertIsNone(self.store.claim("third", lease_seconds=1, now=100.5))
        recovered = self.store.claim("third", lease_seconds=1, now=101.1)
        self.assertEqual(recovered["job_id"], job["job_id"])
        self.assertEqual(recovered["attempts"], 2)
        self.assertFalse(self.store.complete(job["job_id"], claimed[0]["claim_token"], "succeeded", "case:x", "effect:x"))
        self.assertTrue(self.store.complete(job["job_id"], recovered["claim_token"], "succeeded", "case:x", "effect:x"))
        other.close()

    def test_expired_claim_recovery_stops_at_attempt_bound(self):
        job = self.store.submit("tenant-a", "bounded-claims", {"case_id": "C01"}, "t", "r")[0]
        first = self.store.claim("one", lease_seconds=1, max_attempts=2, now=100.0)
        second = self.store.claim("two", lease_seconds=1, max_attempts=2, now=101.1)
        exhausted = self.store.claim("three", lease_seconds=1, max_attempts=2, now=102.2)
        final = self.store.get_job("tenant-a", job["job_id"])
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(exhausted)
        self.assertEqual((final["state"], final["attempts"], final["error_class"]),
                         ("failed", 2, "claim_attempts_exhausted"))
        self.assertFalse(self.store.complete(job["job_id"], second["claim_token"], "succeeded", "case:x", "effect:x"))

    def test_worker_renews_claim_while_processing_past_original_lease(self):
        job = self.store.submit("tenant-a", "long-running", {"case_id": "C12"}, "t", "r")[0]
        other = GatewayStore(self.path)
        artifact_dir = Path(self.tmp.name) / "artifacts"
        entered = threading.Event()
        release = threading.Event()
        calls = 0
        call_lock = threading.Lock()
        outcome = []
        failures = []
        original_run_demo = gateway_module.Lab.run_demo

        def blocking_run_demo(lab, reset=False):
            nonlocal calls
            with call_lock:
                calls += 1
                call_number = calls
            if call_number == 1:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test worker was not released")
            return original_run_demo(lab, reset=reset)

        def process_first():
            try:
                outcome.append(GatewayWorker(self.store, artifact_dir).process_one("slow", lease_seconds=1))
            except Exception as exc:
                failures.append(exc)

        with mock.patch.object(gateway_module.Lab, "run_demo", blocking_run_demo):
            thread = threading.Thread(target=process_first)
            thread.start()
            self.assertTrue(entered.wait(2))
            original_expiry = self.store.get_job("tenant-a", job["job_id"])["lease_expires_at"]
            while time.time() <= original_expiry + 0.1:
                time.sleep(0.02)
            contender = GatewayWorker(other, artifact_dir).process_one("contender", lease_seconds=1)
            release.set()
            thread.join(5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertIsNone(contender)
        self.assertEqual(calls, 1)
        self.assertEqual(outcome[0]["state"], "succeeded")
        final = self.store.get_job("tenant-a", job["job_id"])
        self.assertEqual((final["state"], final["attempts"]), ("succeeded", 1))
        tenant_databases = list((Path(self.tmp.name) / "gateway-tenants").glob("*.db"))
        self.assertEqual(len(tenant_databases), 1)
        db = sqlite3.connect(tenant_databases[0])
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM effects WHERE case_id='C12'").fetchone()[0], 1)
        finally:
            db.close()
            other.close()

    def test_worker_stops_heartbeat_when_processing_is_interrupted(self):
        job = self.store.submit("tenant-a", "interrupted", {"case_id": "C01"}, "t", "r")[0]
        worker = GatewayWorker(self.store, Path(self.tmp.name) / "artifacts")
        with mock.patch.object(gateway_module.Lab, "run_demo", side_effect=SystemExit("stop")):
            with self.assertRaisesRegex(SystemExit, "stop"):
                worker.process_one("interrupted-worker", lease_seconds=1)
        self.assertFalse(any(thread.name == f"gateway-lease-{job['job_id']}" and thread.is_alive()
                             for thread in threading.enumerate()))

    def test_restart_persists_queued_and_terminal_jobs(self):
        queued = self.store.submit("tenant-a", "queued", {"case_id": "C01"}, "t", "r")[0]
        terminal = self.store.submit("tenant-a", "terminal", {"case_id": "C02"}, "t", "r")[0]
        claim = self.store.claim("worker", 30)
        self.store.complete(claim["job_id"], claim["claim_token"], "succeeded", "case:C01", None)
        self.store.close()
        self.store = GatewayStore(self.path)
        states = {self.store.get_job("tenant-a", queued["job_id"])["state"], self.store.get_job("tenant-a", terminal["job_id"])["state"]}
        self.assertEqual(states, {"queued", "succeeded"})

    def test_worker_uses_lab_path_and_classifies_injected_timeout(self):
        good = self.store.submit("tenant-a", "good", {"case_id": "C01"}, "t", "r")[0]
        worker = GatewayWorker(self.store, Path(self.tmp.name) / "artifacts", max_attempts=2)
        result = worker.process_one("worker")
        self.assertEqual(result["state"], "succeeded")
        done = self.store.get_job("tenant-a", good["job_id"])
        self.assertEqual(done["acceptance_id"], "C01")
        failed = self.store.submit("tenant-a", "timeout", {"case_id": "C03", "inject_fault": "proposal_timeout"}, "t", "r")[0]
        self.assertEqual(worker.process_one("worker")["state"], "retry_wait")
        self.store.make_retry_due(failed["job_id"])
        self.assertEqual(worker.process_one("worker")["state"], "failed")
        self.assertEqual(self.store.get_job("tenant-a", failed["job_id"])["error_class"], "proposal_timeout")

    def test_structured_events_are_correlated_and_redacted(self):
        job = self.store.submit("tenant-a", "secretish", {"case_id": "C01"}, "0123456789abcdef0123456789abcdef", "request-1") [0]
        self.store.record_event("request", tenant_id="tenant-a", job_id=job["job_id"], trace_id="0" * 32,
                                span_id="1" * 16, request_id="request-1", state_transition="queued",
                                duration_ms=1.2, error_class=None, extra={"authorization": "hidden", "nested": {"token": "also-hidden"}})
        encoded = json.dumps(self.store.events())
        self.assertNotIn("hidden", encoded)
        event = self.store.events()[-1]
        self.assertEqual(len(event["trace_id"]), 32)
        self.assertNotEqual(event["tenant_safe_id"], "tenant-a")
        self.assertIn("duration_ms", event)


class GatewayHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = make_gateway_server("127.0.0.1", 0, Path(self.tmp.name) / "gateway.db",
                                          Path(self.tmp.name) / "artifacts", {"alpha": "tenant-a", "beta": "tenant-b"},
                                          max_request_bytes=128, rate_limit=20, rate_window_seconds=60)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, token=None, body=None, headers=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        merged = dict(headers or {})
        if token is not None: merged["Authorization"] = "Bearer " + token
        payload = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        if body is not None and "Content-Type" not in merged: merged["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=merged)
        response = conn.getresponse(); raw = response.read(); response_headers = dict(response.getheaders()); conn.close()
        return response.status, json.loads(raw), response_headers

    def test_authentication_strict_json_request_ids_traceparent_and_tenant_scope(self):
        status, error, _ = self.request("POST", "/v1/jobs", body={"case_id": "C01"}, headers={"Idempotency-Key": "k1"})
        self.assertEqual((status, error["error"]["code"]), (401, "unauthorized"))
        traceparent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
        status, created, headers = self.request("POST", "/v1/jobs", "alpha", {"case_id": "C01"}, {"Idempotency-Key": "k1", "traceparent": traceparent, "X-Request-ID": "req-1"})
        self.assertEqual(status, 202)
        self.assertEqual(headers["X-Request-ID"], "req-1")
        self.assertEqual(headers["traceparent"].split("-")[1], traceparent.split("-")[1])
        job_id = created["job"]["job_id"]
        self.assertNotIn("tenant_id", created["job"])
        self.assertEqual(self.request("GET", f"/v1/jobs/{job_id}", "beta")[0], 404)
        self.assertEqual(self.request("GET", "/v1/jobs?limit=100", "beta")[1]["jobs"], [])
        collision = self.request("POST", "/v1/jobs", "beta", {"case_id": "C02"}, {"Idempotency-Key": "k1"})
        self.assertEqual(collision[0], 202)
        self.assertNotEqual(collision[1]["job"]["job_id"], job_id)

    def test_duplicate_delivery_conflict_and_strict_fields(self):
        headers = {"Idempotency-Key": "key"}
        first = self.request("POST", "/v1/jobs", "alpha", {"case_id": "C01"}, headers)
        duplicate = self.request("POST", "/v1/jobs", "alpha", {"case_id": "C01"}, headers)
        self.assertEqual(first[1]["job"]["job_id"], duplicate[1]["job"]["job_id"])
        self.assertEqual(duplicate[0], 200)
        conflict = self.request("POST", "/v1/jobs", "alpha", {"case_id": "C02"}, headers)
        self.assertEqual((conflict[0], conflict[1]["error"]["code"]), (409, "idempotency_conflict"))
        extra = self.request("POST", "/v1/jobs", "alpha", {"case_id": "C01", "tenant_id": "tenant-b"}, {"Idempotency-Key": "extra"})
        self.assertEqual((extra[0], extra[1]["error"]["code"]), (400, "invalid_request"))
        wrong_type = self.request("POST", "/v1/jobs", "alpha", b"{}", {"Idempotency-Key": "type", "Content-Type": "text/plain"})
        self.assertEqual(wrong_type[0], 415)

    def test_malformed_oversized_bad_trace_and_rate_limit(self):
        malformed = self.request("POST", "/v1/jobs", "alpha", b"{", {"Idempotency-Key": "bad", "Content-Type": "application/json"})
        self.assertEqual(malformed[1]["error"]["code"], "malformed_json")
        oversized = self.request("POST", "/v1/jobs", "alpha", b"{" + b" " * 130 + b"}", {"Idempotency-Key": "large", "Content-Type": "application/json"})
        self.assertEqual(oversized[0], 413)
        invalid_trace = self.request("GET", "/v1/jobs", "alpha", headers={"traceparent": "not-valid"})
        self.assertEqual(invalid_trace[1]["error"]["code"], "invalid_traceparent")
        limited = make_gateway_server("127.0.0.1", 0, Path(self.tmp.name) / "limited.db", Path(self.tmp.name) / "limited", {"alpha": "tenant-a"}, rate_limit=1)
        thread = threading.Thread(target=limited.serve_forever, daemon=True); thread.start()
        host, port = limited.server_address
        conn = http.client.HTTPConnection(host, port); conn.request("GET", "/v1/jobs", headers={"Authorization": "Bearer alpha"}); conn.getresponse().read(); conn.close()
        conn = http.client.HTTPConnection(host, port); conn.request("GET", "/v1/jobs", headers={"Authorization": "Bearer alpha"}); response = conn.getresponse(); body = json.loads(response.read()); conn.close()
        self.assertEqual((response.status, body["error"]["code"]), (429, "rate_limited"))
        limited.shutdown(); limited.server_close(); thread.join()

    def test_health_readiness_and_database_interruption(self):
        self.assertEqual(self.request("GET", "/health")[0], 200)
        self.assertEqual(self.request("GET", "/ready")[0], 200)
        self.server.gateway_store.close()
        self.assertEqual(self.request("GET", "/ready")[0], 503)

    def test_worker_is_not_an_http_route(self):
        status, body, _ = self.request("POST", "/v1/worker", "alpha", {})
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))


class AuthenticationAndMigrationTest(unittest.TestCase):
    def test_authenticator_uses_digests_and_constant_time_comparison(self):
        auth = Authenticator({"short-token": "tenant-a"})
        self.assertEqual(auth.authenticate("Bearer short-token"), "tenant-a")
        self.assertIsNone(auth.authenticate("Bearer wrong"))
        self.assertNotIn("short-token", repr(auth))
        self.assertTrue(all(len(digest) == 64 for digest in auth._bindings))

    def test_token_bindings_load_only_from_runtime_json(self):
        self.assertEqual(load_token_bindings('{"one":"tenant-a"}'), {"one": "tenant-a"})
        with self.assertRaises(ValueError):
            load_token_bindings('{"":"tenant-a"}')
        with self.assertRaises(ValueError):
            load_token_bindings('["not-a-map"]')

    def test_load_harness_reports_measured_latency_and_declared_workload(self):
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 95), 4.0)
        with tempfile.TemporaryDirectory() as td:
            result = run_local_load(Path(td), requests=12, concurrency=3)
        self.assertEqual(result["workload"]["requests"], 12)
        self.assertEqual(result["workload"]["concurrency"], 3)
        self.assertEqual(result["completed"], 12)
        self.assertEqual(result["errors"], 0)
        self.assertGreater(result["throughput_requests_per_second"], 0)
        self.assertGreaterEqual(result["latency_ms"]["p95"], result["latency_ms"]["p50"])
        self.assertEqual(result["label"], "local_synthetic_benchmark_not_capacity_or_slo_evidence")

    def test_disposable_upgrade_and_rollback_preserve_core_data(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "migration.db"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE cases(case_id TEXT PRIMARY KEY)")
            db.execute("INSERT INTO cases VALUES('preserved')")
            db.commit()
            upgrade_gateway(db)
            self.assertEqual(db.execute("SELECT version FROM gateway_schema_version").fetchone()[0], 1)
            self.assertIn("gateway_jobs", {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            rollback_gateway(db)
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("gateway_jobs", tables)
            self.assertEqual(db.execute("SELECT case_id FROM cases").fetchone()[0], "preserved")
            db.close()


if __name__ == "__main__":
    unittest.main()
