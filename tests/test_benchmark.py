import json
import tempfile
import unittest
from pathlib import Path

from aal.benchmark import run_benchmark


class BenchmarkTest(unittest.TestCase):
    def test_required_comparisons_and_metrics_are_executable(self):
        with tempfile.TemporaryDirectory() as td:
            result = run_benchmark(Path(td))
            self.assertIn("lexical_only", result["comparisons"]["retrieval"])
            self.assertIn("hybrid_reranked", result["comparisons"]["retrieval"])
            lexical = result["comparisons"]["retrieval"]["lexical_only"]["citation_accuracy"]
            hybrid = result["comparisons"]["retrieval"]["hybrid_reranked"]["citation_accuracy"]
            self.assertGreater(hybrid, lexical)
            self.assertGreaterEqual(hybrid, 0.9)
            self.assertGreater(result["comparisons"]["retry"]["naive_retry"]["duplicate_effect_rate"], 0)
            self.assertEqual(result["comparisons"]["retry"]["effect_safe"]["duplicate_effect_rate"], 0)
            self.assertGreater(result["comparisons"]["verification"]["self_attested"]["false_accept_rate"], 0)
            self.assertEqual(result["metrics"]["false_accept_rate"], 0)
            self.assertEqual(result["metrics"]["unsupported_conclusion_rate"], 0)
            self.assertEqual(result["metrics"]["recovery_success"], 1.0)
            expected = {"accepted_output_rate","false_accept_rate","unsupported_conclusion_rate","citation_accuracy","duplicate_effect_rate","first_pass_acceptance","human_review_count","human_review_minutes_estimate","recovery_success","p50_latency_ms","p95_latency_ms","estimated_cost_per_accepted_item_usd"}
            self.assertEqual(set(result["metrics"]), expected)
            saved = json.loads((Path(td) / "benchmark.json").read_text())
            self.assertEqual(saved["run_id"], result["run_id"])
            self.assertTrue((Path(td) / "benchmark_receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
