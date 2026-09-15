"""Formerly blind inputs, now frozen regression cases; not new blind evidence."""
import hashlib
from pathlib import Path
import unittest

from aal.evaluation import compare, evaluate, load

FIXTURES = Path(__file__).parent / "fixtures" / "blind_reversals"

# Separate adjudication preserves the author's frozen (incorrect) failure labels.
# Absence of support/history is indeterminate under the pre-existing contract,
# unlike affirmative contrary evidence. This was noted before first execution.
ADJUDICATIONS = {"M04": "review_required", "M07": "review_required"}
EXPECTED_FINDINGS = {
    "M01": {}, "M02": {}, "M03": {"exact_reversal": "fail"},
    "M04": {"duplicate_support": "indeterminate"},
    "M05": {"duplicate_support": "fail"}, "M06": {"prior_reversal": "fail"},
    "M07": {"prior_reversal": "indeterminate"}, "M08": {"scope": "fail"},
    "M09A": {"exact_reversal": "fail"}, "M09B": {},
}


class BlindReversalRegressionTest(unittest.TestCase):
    def test_frozen_trial_and_correction_pair(self):
        hashes = load(FIXTURES / "frozen-hashes.json")
        manifest = load(FIXTURES / "manifest.json")
        self.assertEqual({m["member_id"] for m in manifest["members"]}, set(EXPECTED_FINDINGS))
        self.assertEqual(len(manifest["members"]), 10)
        members = {"manifest.json"} | {m[k] for m in manifest["members"] for k in ("case", "proposal")}
        self.assertEqual(set(hashes), members)
        for filename, expected in hashes.items():
            self.assertEqual(hashlib.sha256((FIXTURES / filename).read_bytes()).hexdigest(), expected, filename)
        outputs = {}
        disagreements = set()
        for member in manifest["members"]:
            mid = member["member_id"]
            with self.subTest(member=mid):
                result = evaluate(load(FIXTURES / member["case"]), load(FIXTURES / member["proposal"]))
                outputs[mid] = result
                expected = ADJUDICATIONS.get(mid, member["expected_outcome"])
                self.assertEqual(result["outcome"], expected)
                actual_findings = {f["code"]: f["status"] for f in result["findings"] if f["status"] != "pass"}
                self.assertEqual(actual_findings, EXPECTED_FINDINGS[mid])
                if result["outcome"] != member["expected_outcome"]:
                    disagreements.add(mid)
        self.assertEqual(disagreements, set(ADJUDICATIONS))
        delta = compare(outputs["M09A"], outputs["M09B"])
        self.assertTrue(delta["comparable"])
        self.assertTrue(delta["proposal_changed"])
        self.assertFalse(delta["review_changed"])
        self.assertEqual(delta["before"], "checks_failed")
        self.assertEqual(delta["after"], "checks_passed_within_scope")
        self.assertEqual(delta["unresolved"], [])
