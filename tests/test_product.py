import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from aal.app import Lab
from aal.cli import main
from aal.service import make_server
from aal.store import Store


class ProductTest(unittest.TestCase):
    def test_demo_has_all_required_cases_and_writes_packets(self):
        with tempfile.TemporaryDirectory() as td:
            lab = Lab(Path(td) / "state.db", Path(td) / "artifacts")
            summary = lab.run_demo(reset=True)
            self.assertEqual(summary["case_count"], 12)
            self.assertEqual(summary["false_accepts"], 0)
            self.assertEqual(summary["backend"], "recorded_model")
            self.assertTrue((Path(td) / "artifacts" / "demo_summary.json").exists())
            case_types = {c["case_type"] for c in lab.store.cases()}
            self.assertIn("unsupported_accrual", case_types)
            self.assertIn("retry_after_unknown_external_effect", case_types)
            lab.close()

    def test_two_fresh_demo_runs_have_identical_verdicts_and_separate_telemetry(self):
        with tempfile.TemporaryDirectory() as td:
            roots = [Path(td) / "one", Path(td) / "two"]
            for root in roots:
                lab = Lab(root / "state.db", root / "artifacts")
                lab.run_demo(reset=True); lab.close()
            for case_id in [f"C{i:02d}" for i in range(1, 13)]:
                self.assertEqual((roots[0]/"artifacts"/f"{case_id}.verdict.json").read_bytes(), (roots[1]/"artifacts"/f"{case_id}.verdict.json").read_bytes())
                self.assertEqual((roots[0]/"artifacts"/f"{case_id}.sha256").read_bytes(), (roots[1]/"artifacts"/f"{case_id}.sha256").read_bytes())
                telemetry = json.loads((roots[0]/"artifacts"/f"{case_id}.telemetry.json").read_text())
                self.assertIn("latency_ms", telemetry)
                self.assertNotIn("latency_ms", json.loads((roots[0]/"artifacts"/f"{case_id}.verdict.json").read_text()))

    def test_demo_without_reset_does_not_reapply_recovery_effect(self):
        with tempfile.TemporaryDirectory() as td:
            lab = Lab(Path(td)/"state.db", Path(td)/"artifacts")
            lab.run_demo(reset=True); lab.run_demo(reset=False)
            self.assertEqual(len(lab.store.effects_for_case("C12")), 1)
            self.assertEqual(lab.store.case("C12")["generation"], 2)
            lab.close()

    def test_demo_execution_ignores_expected_fixture_classification(self):
        with tempfile.TemporaryDirectory() as td:
            lab = Lab(Path(td)/"state.db", Path(td)/"artifacts")
            lab.fixtures[0]["expected_case_type"] = "suspicious_unresolved_item"
            lab.run_demo(reset=True)
            self.assertEqual(lab.store.case("C01")["case_type"], "clean_match")
            lab.close()

    def test_mixed_reconciliation_with_residual_is_not_accepted_as_clean(self):
        with tempfile.TemporaryDirectory() as td:
            lab = Lab(Path(td)/"state.db", Path(td)/"artifacts")
            records = [
                {"record_id":"g1","source":"gl","amount":"10","date":"2026-01-01","reference":"A","metadata":{"query":"missing invoice source support","policy_version":"v2","source_support":"missing"}},
                {"record_id":"b1","source":"bank","amount":"-10","date":"2026-01-01","reference":"A"},
                {"record_id":"g2","source":"gl","amount":"20","date":"2026-01-01","reference":"B","metadata":{}},
            ]
            lab.fixtures = [{"case_id":"mixed","expected_case_type":"missing_source","records":records}]
            lab.run_demo(reset=True)
            self.assertNotEqual(lab.store.case("mixed")["case_type"], "clean_match")
            self.assertNotEqual(lab.store.case("mixed")["state"], "accepted")
            lab.close()

    def test_health_and_state_survive_service_restart(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "state.db"
            store = Store(db)
            store.upsert_case("persist-me", "clean_match", 1, "accepted", 1)
            store.close()
            server = make_server("127.0.0.1", 0, db, Path(td) / "artifacts")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            health = json.load(urllib.request.urlopen(base + "/health"))
            cases = json.load(urllib.request.urlopen(base + "/api/cases"))
            self.assertEqual(health["status"], "ok")
            self.assertEqual(cases[0]["case_id"], "persist-me")
            html = urllib.request.urlopen(base + "/").read().decode()
            self.assertIn("Accounting Acceptance Lab", html)
            self.assertIn("Evidence & citations", html)
            server.shutdown(); server.server_close(); thread.join()

    def test_health_fails_closed_when_sqlite_probe_fails(self):
        with tempfile.TemporaryDirectory() as td:
            server = make_server("127.0.0.1", 0, Path(td)/"state.db", Path(td)/"artifacts")
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            server.lab.store.close()
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(base + "/health")
            self.assertEqual(caught.exception.code, 503)
            self.assertEqual(json.loads(caught.exception.read())["status"], "error")
            server.shutdown(); server.server_close(); thread.join()

    def test_event_log_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "state.db")
            store.event("probe", {"api_key":"forbidden-value", "case_id":"safe"})
            self.assertNotIn("forbidden-value", store.events()[0]["payload"])
            self.assertIn("[REDACTED]", store.events()[0]["payload"])
            store.close()

    def test_cli_approval_records_decision_without_applying_effect(self):
        with tempfile.TemporaryDirectory() as td:
            lab = Lab(Path(td) / "lab.db", Path(td) / "artifacts")
            lab.run_demo(reset=True)
            lab.close()
            with patch("builtins.print") as output:
                main(["--state-dir", td, "approve", "C03", "--actor", "local-operator", "--reason", "reviewed"])
            result = json.loads(output.call_args.args[0])
            self.assertEqual(result["approval_lifecycle"]["decision_recorded"], "approved")
            self.assertFalse(result["approval_lifecycle"]["effect_applied"])
            self.assertEqual(result["effects"], [])


if __name__ == "__main__":
    unittest.main()
