import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aal.batch import compare_batches, evaluate_batch, exit_code, format_batch_text
from aal.evaluation import digest, load, review

ROOT = Path(__file__).resolve().parents[1]


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case = load(ROOT / "examples/reversal-case.json")
        self.case["policy"]["require_review"] = False
        self.proposal = load(ROOT / "examples/reversal-proposal.json")
        self.write("case.json", self.case)
        self.write("good.json", self.proposal)
        bad = copy.deepcopy(self.proposal)
        bad["lines"][0]["debit"] = "480.00"
        bad["lines"][1]["credit"] = "480.00"
        self.write("bad.json", bad)
        self.manifest = {"schema_version": 1, "name": "test candidate", "members": [
            {"id": "A", "case": "case.json", "proposal": "good.json", "review": None},
            {"id": "B", "case": "case.json", "proposal": "bad.json", "review": None},
        ]}

    def write(self, filename, value):
        path = self.root / filename
        path.write_text(json.dumps(value))
        return path

    def run_batch(self):
        return evaluate_batch(self.write("manifest.json", self.manifest))

    def test_mixed_batch_preserves_errors_and_frozen_denominator(self):
        self.case["history_complete"] = False
        self.write("incomplete.json", self.case)
        self.manifest["members"].extend([
            {"id": "C", "case": "incomplete.json", "proposal": "good.json", "review": None},
            {"id": "D", "case": "case.json", "proposal": "absent.json", "review": None},
            {"id": "E", "case": "case.json", "proposal": "malformed.json", "review": None},
        ])
        (self.root / "malformed.json").write_text('{"x":1,"x":2}')
        result = self.run_batch()
        self.assertEqual(result["summary"], {"total": 5, "checks_passed_within_scope": 1,
                         "checks_failed": 1, "review_required": 1, "invalid_input": 2})
        self.assertEqual([m["id"] for m in result["members"]], ["A", "B", "C", "D", "E"])
        self.assertIn("duplicate JSON key", result["members"][4]["error"])
        self.assertEqual(exit_code(result), 2)
        self.assertIn('Member "D"', format_batch_text(result))
        self.assertIn("invalid_input", format_batch_text(result))
        self.assertFalse(compare_batches(result, result)["comparable"])

    def test_shared_case_read_once(self):
        manifest_path = self.write("manifest.json", self.manifest)
        with patch("aal.batch.load", wraps=load) as reader:
            result = evaluate_batch(manifest_path)
        self.assertEqual(sum(call.args[0] == self.root / "case.json" for call in reader.call_args_list), 1)
        self.assertEqual(result["members"][0]["evaluation"]["binding"]["case_hash"],
                         result["members"][1]["evaluation"]["binding"]["case_hash"])
        self.assertEqual(exit_code(result), 1)

    def test_corrected_version_and_reordered_members(self):
        before = self.run_batch()
        self.manifest["members"][1]["proposal"] = "good.json"
        self.manifest["members"].reverse()
        after = self.run_batch()
        self.assertEqual(exit_code(after), 0)
        delta = compare_batches(before, after)
        self.assertTrue(delta["comparable"])
        self.assertEqual(delta["before_summary"]["checks_failed"], 1)
        self.assertEqual(delta["after_summary"]["checks_failed"], 0)
        b = next(m for m in delta["members"] if m["id"] == "B")["comparison"]
        self.assertEqual((b["before"], b["after"]), ("checks_failed", "checks_passed_within_scope"))
        self.assertEqual(b["unresolved"], [])
        self.assertIn('"exact_reversal": "fail" -> "pass"', format_batch_text(delta))

    def test_dropped_member_and_policy_change_are_not_improvement(self):
        before = self.run_batch()
        self.manifest["members"].pop()
        delta = compare_batches(before, self.run_batch())
        self.assertFalse(delta["comparable"])
        self.assertEqual(delta["removed"], ["B"])
        self.assertIn("NOT a same-basis", format_batch_text(delta))
        self.manifest["members"].append({"id": "new", "case": "case.json", "proposal": "good.json", "review": None})
        delta = compare_batches(before, self.run_batch())
        self.assertEqual(delta["added"], ["new"])
        self.case["policy"]["version"] = "changed"
        self.write("case.json", self.case)
        delta = compare_batches(before, self.run_batch())
        self.assertIn("policy_hash", delta["members"][0]["comparison"]["changed_basis"])

    def test_review_changes_visible_and_stale_review_fails(self):
        self.case["policy"]["require_review"] = True
        self.write("case.json", self.case)
        self.manifest["members"] = self.manifest["members"][:1]
        before = self.run_batch()
        self.assertEqual(exit_code(before), 3)
        disposition = review(self.case, self.proposal, "test actor", "approve", "synthetic test")
        self.write("review.json", disposition)
        self.manifest["members"][0]["review"] = "review.json"
        after = self.run_batch()
        self.assertEqual(exit_code(after), 0)
        self.assertTrue(compare_batches(before, after)["members"][0]["comparison"]["review_changed"])
        self.manifest["members"][0]["proposal"] = "bad.json"
        result = self.run_batch()
        statuses = {f["code"]: f["status"] for f in result["members"][0]["evaluation"]["findings"]}
        self.assertEqual(statuses["review_binding"], "fail")

    def test_invalid_manifests_fail_before_evaluation(self):
        for mutate in (
            lambda m: m.update(members=[]),
            lambda m: m["members"].append(copy.deepcopy(m["members"][0])),
            lambda m: m["members"][0].update(expected_outcome="checks_passed_within_scope"),
            lambda m: m.update(schema_version=True),
        ):
            candidate = copy.deepcopy(self.manifest)
            mutate(candidate)
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                evaluate_batch(self.write("invalid-manifest.json", candidate))

    def test_paths_cannot_escape_manifest_directory(self):
        with tempfile.TemporaryDirectory() as outside:
            foreign = Path(outside) / "case.json"
            foreign.write_text(json.dumps(self.case))
            (self.root / "linked.json").symlink_to(foreign)
            for filename in (str(foreign), os.path.relpath(foreign, self.root), "linked.json"):
                with self.subTest(filename=filename):
                    self.manifest["members"][0]["case"] = filename
                    result = self.run_batch()
                    self.assertIn("inside manifest directory", result["members"][0]["error"])
                    self.assertEqual(result["summary"]["invalid_input"], 1)

    def test_corrupted_results_are_rejected(self):
        result = self.run_batch()
        changed = copy.deepcopy(result)
        changed["summary"]["checks_failed"] = 0
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            compare_batches(result, changed)
        changed["result_hash"] = digest({k: v for k, v in changed.items() if k != "result_hash"})
        with self.assertRaisesRegex(ValueError, "summary mismatch"):
            compare_batches(result, changed)

    def test_null_review_and_nonregular_file_are_visible_errors(self):
        self.write("review.json", None)
        self.manifest["members"][0]["review"] = "review.json"
        self.manifest["members"][1]["proposal"] = "."
        result = self.run_batch()
        self.assertEqual(result["summary"]["invalid_input"], 2)
        self.assertIn("not null", result["members"][0]["error"])
        self.assertIn("regular input file", result["members"][1]["error"])

    def test_bundled_rehearsal_preserves_unresolved_support(self):
        fixtures = ROOT / "tests/fixtures/blind_reversals"
        before = evaluate_batch(fixtures / "batch-before.json")
        after = evaluate_batch(fixtures / "batch-after.json")
        self.assertEqual(before["summary"], {"total": 3, "checks_passed_within_scope": 1,
                         "checks_failed": 1, "review_required": 1, "invalid_input": 0})
        self.assertEqual(after["summary"], {"total": 3, "checks_passed_within_scope": 2,
                         "checks_failed": 0, "review_required": 1, "invalid_input": 0})
        self.assertEqual(exit_code(after), 3)
        delta = compare_batches(before, after)
        self.assertTrue(delta["comparable"])
        unresolved = next(m for m in delta["members"] if m["id"] == "missing-support")["comparison"]["unresolved"]
        self.assertEqual([(f["code"], f["status"]) for f in unresolved], [("duplicate_support", "indeterminate")])

    def test_cli_saves_error_members_and_preserves_previous_result(self):
        manifest = self.write("manifest.json", self.manifest)
        def run(*args):
            return subprocess.run([sys.executable, "-m", "aal.cli", *map(str, args)], cwd=self.root,
                                  env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True)
        before = self.root / "before.json"
        r = run("evaluate-batch", "--manifest", manifest, "--out", before, "--format", "text")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn('Member "B"', r.stdout)
        saved = before.read_bytes()
        self.assertEqual(run("evaluate-batch", "--manifest", manifest, "--out", before).returncode, 2)
        self.assertEqual(saved, before.read_bytes())
        self.manifest["members"][1]["proposal"] = "good.json"
        self.write("manifest.json", self.manifest)
        after = self.root / "after.json"
        r = run("evaluate-batch", "--manifest", manifest, "--out", after)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), load(after))
        r = run("compare-batches", before, after, "--format", "text")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"exact_reversal": "fail" -> "pass"', r.stdout)
        self.manifest["members"][1]["proposal"] = "missing.json"
        self.write("manifest.json", self.manifest)
        errors = self.root / "errors.json"
        r = run("evaluate-batch", "--manifest", manifest, "--out", errors)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(load(errors)["summary"]["invalid_input"], 1)
        self.assertFalse((self.root / ".state").exists())
