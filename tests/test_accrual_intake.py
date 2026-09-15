import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import openpyxl

from aal.accrual_intake import reconcile_workbook

ROOT = Path(__file__).resolve().parents[1]


class WorkbookIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "source.xlsx"
        self.mapping = json.loads((ROOT / "examples/accrual-workbook-mapping.json").read_text())
        self.book = openpyxl.Workbook()
        self.addCleanup(self.book.close)
        self.sheet = self.book.active
        self.sheet.title = "Accrual"
        for row in [
            ["Synthetic prior-year revenue accrual", None, None, "2025-12-31"],
            ["Reversal", 920.50, datetime(2025, 7, 1)],
            ["Prior-year portion collected", -120.25, datetime(2025, 8, 20)],
            ["Outstanding A", 300.10], ["Outstanding B", 500.15], ["Already included", 200.00],
        ]:
            self.sheet.append(row)
        self.book.save(self.path)

    def run_case(self, amount="600.25"):
        return reconcile_workbook(self.path, self.mapping, amount, "test-preparer")

    def test_extracts_amounts_signs_dates_and_source_identity_without_writing(self):
        before = self.path.read_bytes()
        result = self.run_case()
        self.assertEqual(result["outcome"], "checks_passed_within_scope")
        self.assertEqual(result["reconciliation"]["total_addback"], "800.25")
        self.assertEqual(result["reconciliation"]["expected_additional_adjustment"], "600.25")
        self.assertEqual(result["intake"]["case"]["collections"][0]["date"], "2025-08-20")
        self.assertEqual(result["intake"]["workbook_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(result["intake"]["cells"]["Accrual!B3"], -120.25)
        self.assertEqual(self.path.read_bytes(), before)

    def test_zero_credit_is_zero_not_an_invalid_negative_amount(self):
        self.sheet["B3"] = 0
        self.sheet["B5"] = 620.40
        self.book.save(self.path)
        result = self.run_case("720.50")
        self.assertEqual(result["outcome"], "checks_passed_within_scope")
        self.assertEqual(result["reconciliation"]["prior_period_collections"], "0.00")

    def test_same_mapping_handles_second_workbook_with_different_amounts(self):
        first = self.run_case()
        mapping_before = copy.deepcopy(self.mapping)
        self.sheet["B2"] = 1010.50
        self.sheet["B5"] = 590.15
        self.path = Path(self.temp.name) / "second.xlsx"
        self.book.save(self.path)
        result = self.run_case("690.25")
        self.assertEqual(result["outcome"], "checks_passed_within_scope")
        self.assertEqual(result["reconciliation"]["outstanding"], "890.25")
        self.assertEqual(self.mapping, mapping_before)
        self.assertNotEqual(first["case_hash"], result["case_hash"])
        self.assertEqual(first["intake"]["mapping_hash"], result["intake"]["mapping_hash"])

    def test_formula_amounts_and_blank_text_bool_precision_and_huge_values_stop(self):
        for value in ("=SUM(B4:B5)", None, "920.50", True, 920.501, 1e100):
            with self.subTest(value=value):
                self.sheet["B2"] = value
                self.book.save(self.path)
                with self.assertRaises(ValueError):
                    self.run_case()

    def test_changed_layout_or_control_stops_before_amount_acceptance(self):
        for ref, value in (("A3", "Current-year revenue collected"), ("D1", "2026-03-31")):
            with self.subTest(ref=ref):
                original = self.sheet[ref].value
                self.sheet[ref] = value
                self.book.save(self.path)
                with self.assertRaisesRegex(ValueError, "source/layout assertion changed"):
                    self.run_case()
                self.sheet[ref] = original

    def test_guarded_literal_sum_does_not_evaluate_formula(self):
        self.sheet["B6"] = "=SUM(B4:B5)"
        self.book.save(self.path)
        self.mapping["checks"].append({"ref": {"sheet": "Accrual", "cell": "B6"}, "equals": "=SUM(B4:B5)"})
        self.mapping["already_reflected"]["amount"]["cells"] = [{"sheet": "Accrual", "cell": "B4"}, {"sheet": "Accrual", "cell": "B5"}]
        self.assertEqual(self.run_case("0.00")["outcome"], "checks_passed_within_scope")
        self.sheet["B6"] = "=SUM(B4:B5)+1"
        self.book.save(self.path)
        with self.assertRaisesRegex(ValueError, "source/layout assertion changed"):
            self.run_case("0.00")

    def test_duplicate_cells_bad_addresses_and_missing_guards_rejected(self):
        original = copy.deepcopy(self.mapping)
        self.mapping["reversal"]["amount"]["cells"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate amount cell"):
            self.run_case()
        for address in ("A0", "A1:B2", "XFE1", "A1048577", "a1"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.mapping = copy.deepcopy(original)
                self.mapping["reversal"]["amount"]["cells"][0]["cell"] = address
                self.run_case()
        self.mapping = original
        self.mapping["checks"] = []
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.run_case()

    def test_changed_unselected_source_changes_binding_and_unknown_history_stays_open(self):
        first = self.run_case()
        self.sheet["F10"] = "New contextual evidence"
        self.book.save(self.path)
        second = self.run_case()
        self.assertEqual(first["reconciliation"], second["reconciliation"])
        self.assertNotEqual(first["case_hash"], second["case_hash"])
        self.mapping["history_complete"] = False
        result = self.run_case()
        self.assertEqual(result["outcome"], "review_required")

    def test_cli_exit_codes_preserves_output_and_rejects_mixed_modes(self):
        mapping_path = Path(self.temp.name) / "mapping.json"
        mapping_path.write_text(json.dumps(self.mapping))
        dest = Path(self.temp.name) / "result.json"
        base = [sys.executable, "-m", "aal.cli", "reconcile-accrual", "--workbook", str(self.path),
                "--mapping", str(mapping_path), "--producer", "tester", "--out", str(dest)]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        run = subprocess.run(base + ["--additional-adjustment", "800.25"], env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 1, run.stderr)
        before = dest.read_bytes()
        run = subprocess.run(base + ["--additional-adjustment", "600.25"], env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(dest.read_bytes(), before)
        for extra in ([], ["--additional-adjustment", "0.00", "--proposal", "not-used.json"]):
            run = subprocess.run(base + extra, env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertNotIn("Traceback", run.stderr)

    def test_json_mode_still_runs_without_optional_xlsx_dependency(self):
        base = [sys.executable, "-S", "-m", "aal.cli", "reconcile-accrual"]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        dest = str(Path(self.temp.name) / "without-xlsx.json")
        run = subprocess.run(base + ["--case", str(ROOT / "examples/accrual-case.json"),
                                     "--proposal", str(ROOT / "examples/accrual-proposal.json"), "--out", dest],
                             env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        run = subprocess.run(base + ["--workbook", str(self.path),
                                     "--mapping", str(ROOT / "examples/accrual-workbook-mapping.json"),
                                     "--additional-adjustment", "0.00", "--producer", "tester", "--out", dest],
                             env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn("optional dependency", run.stderr)
        self.assertNotIn("Traceback", run.stderr)

    def test_invalid_workbook_and_nonmidnight_date_are_actionable_errors(self):
        self.sheet["C2"] = datetime(2025, 7, 1, 12, 30)
        self.book.save(self.path)
        with self.assertRaisesRegex(ValueError, "ISO date"):
            self.run_case()
        self.path.write_bytes(b"not a workbook")
        with self.assertRaisesRegex(ValueError, "valid, unencrypted XLSX"):
            self.run_case()
