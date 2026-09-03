import json
import tempfile
import threading
import unittest
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
