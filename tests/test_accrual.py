import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from aal.accrual import evaluate_accrual, format_accrual_text

ROOT = Path(__file__).resolve().parents[1]


class AccrualTests(unittest.TestCase):
    def setUp(self):
        self.case = json.loads((ROOT / "examples/accrual-case.json").read_text())
        self.proposal = json.loads((ROOT / "examples/accrual-proposal.json").read_text())

    def result(self):
        return evaluate_accrual(self.case, self.proposal)

    def test_authored_synthetic_acceptance_scenarios(self):
        fixtures = json.loads((ROOT / "examples/accrual-synthetic-scenarios.json").read_text())
        scenarios = fixtures["scenarios"]
        self.assertEqual([s["id"] for s in scenarios], ["SYN-A01", "SYN-A02", "SYN-A03", "SYN-A04", "SYN-A05"])
        for scenario in scenarios:
            with self.subTest(case=scenario["id"], treatment=scenario["treatment"]):
                case, proposal = copy.deepcopy(self.case), copy.deepcopy(self.proposal)
                case["case_id"] = proposal["case_id"] = scenario["id"]
                case["reversal"]["amount"] = scenario["reversal"]
                case["collections"][0]["amount"] = scenario["collection"]
                self.assertEqual(len(scenario["outstanding"]), len(case["outstanding"]))
                for item, amount in zip(case["outstanding"], scenario["outstanding"], strict=True):
                    item["amount"] = amount
                if scenario["already_reflected"] is None:
                    case["already_reflected"] = None
                else:
                    case["already_reflected"]["amount"] = scenario["already_reflected"]
                case["support_as_of"] = scenario["support_as_of"]
                case["history_complete"] = scenario["history_complete"]
                proposal["additional_adjustment"] = scenario["proposed_additional"]
                proposal["producer"] = "synthetic-fixture-not-human-review"
                result = evaluate_accrual(case, proposal)
                expected = scenario["expected"]
                self.assertEqual(result["outcome"], expected["outcome"])
                self.assertEqual(result["reconciliation"]["total_addback"], expected["total_addback"])
                self.assertEqual(result["reconciliation"]["expected_additional_adjustment"], expected["additional"])
                self.assertEqual({c["id"]: c["status"] for c in result["checks"] if c["status"] != "pass"}, expected["unresolved"])

    def test_partial_existing_adjustment_and_split_balances(self):
        result = self.result()
        self.assertEqual(result["outcome"], "checks_passed_within_scope")
        self.assertEqual(result["reconciliation"]["total_addback"], "800.25")
        self.assertEqual(result["reconciliation"]["expected_additional_adjustment"], "600.25")

    def test_total_is_not_incremental_and_negative_correction(self):
        self.proposal["additional_adjustment"] = "800.25"
        self.assertEqual(self.result()["outcome"], "checks_failed")
        self.case["already_reflected"]["amount"] = "850.00"
        self.proposal["additional_adjustment"] = "-49.75"
        self.assertEqual(self.result()["outcome"], "checks_passed_within_scope")

    def test_missing_existing_amount_is_not_zero(self):
        self.case["already_reflected"] = None
        self.proposal["additional_adjustment"] = "800.25"
        self.assertEqual(self.result()["outcome"], "review_required")

    def test_sources_survive_json_roundtrip_and_escape_terminal_controls(self):
        self.case["reversal"]["source"] = "source\x1b[2J\nline"
        result = self.result()
        self.assertEqual(json.loads(json.dumps(result)), result)
        self.assertIn("source\x1b[2J\nline", result["source_references"])
        output = format_accrual_text(result)
        self.assertNotIn("\x1b", output)
        self.assertIn(r"source\u001b[2J\nline", output)

    def test_source_balance_disagreement_is_retained(self):
        self.case["outstanding"][1]["amount"] = "500.16"
        result = self.result()
        self.assertEqual(result["outcome"], "checks_failed")
        tie = next(c for c in result["checks"] if c["id"] == "outstanding_tie")
        self.assertEqual((tie["expected"], tie["observed"]), ("800.25", "800.26"))

    def test_old_or_future_support_and_unknown_history(self):
        for date in ("2025-09-30", "2026-01-01"):
            with self.subTest(date=date):
                self.case["support_as_of"] = date
                self.assertEqual(self.result()["outcome"], "review_required")
        self.case["support_as_of"] = "2025-12-31"
        self.case["history_complete"] = False
        self.assertEqual(self.result()["outcome"], "review_required")

    def test_period_boundaries_and_wrong_scope(self):
        for date in ("2025-06-30", "2026-01-01"):
            with self.subTest(date=date):
                self.proposal["date"] = date
                self.assertEqual(self.result()["outcome"], "checks_failed")
        self.proposal["date"] = "2025-12-31"
        self.case["collections"][0]["date"] = "2026-01-01"
        self.assertEqual(self.result()["outcome"], "checks_failed")
        self.case["collections"][0]["date"] = "2025-12-31"
        self.assertEqual(self.result()["outcome"], "checks_passed_within_scope")
        self.proposal["scope"] = "Other fund"
        self.assertEqual(self.result()["outcome"], "checks_failed")

    def test_duplicate_input_and_invalid_amounts_rejected(self):
        self.case["collections"].append(copy.deepcopy(self.case["collections"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.result()
        self.case["collections"].pop()
        for invalid in (True, 5.00, "NaN", "1.001", "+1.00"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.proposal["additional_adjustment"] = invalid
                self.result()

    def test_cli_create_only_output_and_no_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "result.json"
            state = Path(directory) / "state"
            command = [sys.executable, "-m", "aal.cli", "--state-dir", str(state), "reconcile-accrual",
                       "--case", str(ROOT / "examples/accrual-case.json"),
                       "--proposal", str(ROOT / "examples/accrual-proposal.json"),
                       "--out", str(dest), "--format", "text"]
            env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("600.25", result.stdout)
            original = dest.read_bytes()
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(dest.read_bytes(), original)
            self.assertFalse(state.exists())
