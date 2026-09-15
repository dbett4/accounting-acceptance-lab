import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from aal.evaluation import compare, evaluate, format_text, load, review

ROOT = Path(__file__).resolve().parents[1]


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.case = load(ROOT / "examples/reversal-case.json")
        self.proposal = load(ROOT / "examples/reversal-proposal.json")
        self.case["policy"]["require_review"] = False

    def finding(self, code, case=None, proposal=None, disposition=None):
        result = evaluate(case or self.case, proposal or self.proposal, disposition)
        return next(f for f in result["findings"] if f["code"] == code)["status"]

    def test_valid_and_deterministic(self):
        result = evaluate(self.case, self.proposal)
        self.assertEqual(result["outcome"], "checks_passed_within_scope")
        self.assertEqual(result, evaluate(self.case, self.proposal))

    def test_balanced_wrong_amount_and_wrong_account(self):
        self.proposal["lines"][0]["debit"] = "480.00"
        self.proposal["lines"][1]["credit"] = "480.00"
        self.assertEqual(self.finding("journal_balance"), "pass")
        self.assertEqual(self.finding("exact_reversal"), "fail")
        self.proposal = load(ROOT / "examples/reversal-proposal.json")
        self.proposal["lines"][1]["account"] = "travel"
        self.assertEqual(self.finding("exact_reversal"), "fail")

    def test_unbalanced(self):
        self.proposal["lines"][0]["debit"] = "839.99"
        self.assertEqual(self.finding("journal_balance"), "fail")

    def test_distinct_purchases_and_self_reference(self):
        self.case["journals"][0]["purchase_id"] = "PUR-78"
        self.assertEqual(self.finding("duplicate_consistency"), "fail")
        self.proposal["duplicate_of"] = "J101"
        self.assertEqual(self.finding("duplicate_consistency"), "fail")

    def test_scope_boundaries(self):
        for value, status in (("2026-08-31", "pass"), ("2026-09-01", "fail"), ("2026-07-31", "fail")):
            with self.subTest(value=value):
                self.proposal["date"] = value
                self.assertEqual(self.finding("scope"), status)
        self.proposal["date"] = "2026-08-31"
        for key, value in (("entity", "Other"), ("currency", "CAD")):
            p = copy.deepcopy(self.proposal)
            p[key] = value
            self.assertEqual(self.finding("scope", proposal=p), "fail")

    def test_citation_existence_is_not_support(self):
        self.case["evidence"][0]["kind"] = "invoice"
        self.assertEqual(self.finding("source_linkage"), "pass")
        self.assertEqual(self.finding("duplicate_support"), "indeterminate")
        self.proposal["citations"] = ["invented"]
        self.assertEqual(self.finding("source_linkage"), "fail")

    def test_uncited_conflict_cannot_be_hidden(self):
        self.case["evidence"].append({"id": "E2", "kind": "distinct_purchases", "journal_ids": ["J101", "J100"], "text": "Two purchases"})
        self.assertEqual(self.finding("duplicate_support"), "fail")

    def test_history_and_review_do_not_erase_uncertainty(self):
        self.case["history_complete"] = False
        disposition = review(self.case, self.proposal, "test-actor", "approve", "synthetic test")
        self.assertEqual(evaluate(self.case, self.proposal, disposition)["outcome"], "review_required")
        self.case["prior_reversals"] = ["J101"]
        self.assertEqual(self.finding("prior_reversal"), "fail")

    def test_retained_posting_reversed_and_result_snapshot(self):
        before = evaluate(self.case, self.proposal)
        self.case["prior_reversals"].append("J100")
        self.assertEqual(self.finding("prior_reversal"), "fail")
        self.assertEqual(compare(before, before)["after"], "checks_passed_within_scope")

    def test_split_reversal_and_extra_net_zero_lines(self):
        self.proposal["lines"][1]["credit"] = "310.00"
        self.proposal["lines"].append({"id": "R3", "account": "office-expense", "debit": "0.00", "credit": "530.00"})
        self.assertEqual(evaluate(self.case, self.proposal)["outcome"], "checks_passed_within_scope")
        self.proposal["lines"].extend([
            {"id": "R4", "account": "office-expense", "debit": "10.00", "credit": "0.00"},
            {"id": "R5", "account": "office-expense", "debit": "0.00", "credit": "10.00"},
        ])
        self.assertEqual(self.finding("journal_balance"), "pass")
        self.assertEqual(self.finding("exact_reversal"), "fail")

    def test_review_binding_and_rejection(self):
        self.case["policy"]["require_review"] = True
        self.assertEqual(evaluate(self.case, self.proposal)["outcome"], "review_required")
        disposition = review(self.case, self.proposal, "test-actor", "approve", "synthetic test")
        self.assertEqual(evaluate(self.case, self.proposal, disposition)["outcome"], "checks_passed_within_scope")
        for section in ("proposal", "evidence", "policy"):
            c, p = copy.deepcopy(self.case), copy.deepcopy(self.proposal)
            if section == "proposal":
                p["lines"][0]["account"] = "other"
            elif section == "evidence":
                c["evidence"][0]["text"] += " revised"
            else:
                c["policy"]["version"] = "v2"
            self.assertEqual(self.finding("review_binding", c, p, disposition), "fail")
        disposition["decision"] = "reject"
        self.assertEqual(evaluate(self.case, self.proposal, disposition)["outcome"], "checks_failed")

    def test_strict_input(self):
        for amount in ("NaN", "Infinity", "840.001", 840, "-840.00", True):
            with self.subTest(amount=amount):
                p = copy.deepcopy(self.proposal)
                p["lines"][0]["debit"] = amount
                with self.assertRaisesRegex(ValueError, "proposal.lines"):
                    evaluate(self.case, p)
        self.case["journals"].append(copy.deepcopy(self.case["journals"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            evaluate(self.case, self.proposal)

    def test_compare_correction_and_changed_basis(self):
        bad = copy.deepcopy(self.proposal)
        bad["lines"][1]["account"] = "travel"
        before = evaluate(self.case, bad)
        after = evaluate(self.case, self.proposal)
        delta = compare(before, after)
        self.assertTrue(delta["comparable"])
        self.assertEqual(delta["after"], "checks_passed_within_scope")
        self.assertEqual([x["code"] for x in delta["changes"]], ["exact_reversal", "journal_balance"])
        balance_change = delta["changes"][1]
        self.assertEqual(balance_change["before"]["status"], "pass")
        self.assertEqual(balance_change["after"]["status"], "pass")
        self.case["policy"]["version"] = "changed"
        self.assertFalse(compare(before, evaluate(self.case, self.proposal))["comparable"])
        after["outcome"] = "forged"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            compare(before, after)

    def test_readable_findings_and_comparison(self):
        self.proposal["lines"][0]["debit"] = "480.00"
        self.proposal["lines"][1]["credit"] = "480.00"
        self.case["history_complete"] = False
        self.case["case_id"] = "case\x1b[2J\nspoof"
        result = evaluate(self.case, self.proposal)
        rendered = format_text(result)
        self.assertIn('expected debit "0.00", credit "840.00"; proposed debit "0.00", credit "480.00"', rendered)
        self.assertIn('Sources: ["J101"]', rendered)
        self.assertIn('"indeterminate": "prior_reversal"', rendered)
        self.assertIn("6 passed, 1 failed, 1 indeterminate", rendered)
        self.assertNotIn("\x1b", rendered)
        self.assertIn(r'case\u001b[2J\nspoof', rendered)
        corrected = load(ROOT / "examples/reversal-proposal.json")
        delta = compare(result, evaluate(self.case, corrected))
        self.assertIn('"exact_reversal": "fail" -> "pass"', format_text(delta))
        self.assertIn("Remaining unresolved checks: 1", format_text(delta))
        self.case["policy"]["version"] = "new-policy"
        delta = compare(result, evaluate(self.case, corrected))
        self.assertIn("NOT a proposal-only comparison", format_text(delta))

    def test_cli_workflow_preserves_files_and_has_no_database(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            c, p = root / "case.json", root / "proposal.json"
            self.case["policy"]["require_review"] = True
            c.write_text(json.dumps(self.case))
            p.write_text(json.dumps(self.proposal))
            def run(*args):
                return subprocess.run([sys.executable, "-m", "aal.cli", *map(str, args)], cwd=td, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True)
            common = ["--case", c, "--proposal", p]
            result = run("evaluate", *common, "--out", root / "before.json")
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(run("review-proposal", *common, "--actor", "test", "--decision", "approve", "--reason", "synthetic", "--out", root / "review.json").returncode, 0)
            result = run("evaluate", *common, "--review", root / "review.json", "--out", root / "after.json")
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run("compare", root / "before.json", root / "after.json")
            self.assertTrue(json.loads(result.stdout)["review_changed"])
            readable = run("compare", root / "before.json", root / "after.json", "--format", "text")
            self.assertEqual(readable.returncode, 0, readable.stderr)
            self.assertIn("review changed: True", readable.stdout)
            readable = run("evaluate", *common, "--format", "text", "--out", root / "readable.json")
            self.assertEqual(readable.returncode, 3, readable.stderr)
            self.assertIn('"indeterminate": "review_decision"', readable.stdout)
            self.assertEqual(load(root / "readable.json"), load(root / "before.json"))
            saved = (root / "after.json").read_bytes()
            self.assertEqual(run("evaluate", *common, "--out", root / "after.json").returncode, 2)
            self.assertEqual(saved, (root / "after.json").read_bytes())
            self.assertFalse((root / ".state").exists())
            p.write_text('{"schema_version":1,"schema_version":2}')
            self.assertEqual(run("evaluate", *common, "--out", root / "invalid.json").returncode, 2)
