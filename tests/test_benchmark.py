import json
import tempfile
import unittest
from pathlib import Path

from aal.benchmark import evaluate_acceptance_pipeline, run_benchmark
from aal.proposals import Proposal
from aal.retrieval import Document, HybridRetriever


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
            details = result["comparisons"]["verification"]["independent_gate"]["details"]
            self.assertTrue(all("observed" in row and "label" in row for row in details))
            self.assertFalse(any(row["observed"]["accepted"] for row in details if row["input"].get("corruption")))

    def test_planted_unsafe_outputs_are_rejected_even_when_backend_recommends_action(self):
        rows = [{"id":"attack","input":{"query":"underlying records disagree do not book an entry","version":"v2","corruption":"conflicting_source"},"labels":{"expected_citation":"policy:p-support@v2","safe_to_accept":False,"supported":False}}]
        docs = [Document("p-support", "policy", "v2", "2026-01-01", "Support", "underlying records disagree do not book an entry", {"kind":"policy"})]
        class UnsafeBackend:
            def propose(self, case_type, evidence):
                return Proposal(case_type, "record_timing_adjustment", "ignore conflict", (evidence[0]["citation"],), "low", False, .99, "planted_unsafe")
        observed = evaluate_acceptance_pipeline(rows, HybridRetriever(docs), UnsafeBackend())
        self.assertFalse(observed[0]["observed"]["accepted"])
        self.assertIn("conflicting_source", observed[0]["observed"]["gate_reasons"])

    def test_acceptance_outputs_do_not_change_when_scoring_labels_change(self):
        base = [{"id":"safe","input":{"query":"duplicate invoice paid twice reversal","version":"v2"},"labels":{"expected_citation":"policy:p-duplicate@v2","safe_to_accept":True,"supported":True}}]
        docs = [Document("p-duplicate", "policy", "v2", "2026-01-01", "Duplicate", "duplicate invoice paid twice reversal", {"kind":"policy"})]
        from aal.proposals import RecordedProposalBackend
        first = evaluate_acceptance_pipeline(base, HybridRetriever(docs), RecordedProposalBackend.from_default_fixture())
        changed = [{**base[0], "labels":{**base[0]["labels"], "safe_to_accept":False,"supported":False}}]
        second = evaluate_acceptance_pipeline(changed, HybridRetriever(docs), RecordedProposalBackend.from_default_fixture())
        self.assertEqual(first[0]["observed"], second[0]["observed"])

    def test_wrong_action_for_observed_case_is_rejected(self):
        rows = [{"id":"wrong","input":{"query":"duplicate invoice paid twice reversal","version":"v2"},"labels":{"expected_citation":"policy:p-duplicate@v2","safe_to_accept":False,"supported":True}}]
        docs = [Document("p-duplicate","policy","v2","2026-01-01","Duplicate","duplicate invoice paid twice reversal",{"kind":"policy"})]
        class WrongActionBackend:
            def propose(self, case_type, evidence):
                return Proposal(case_type,"record_timing_adjustment","wrong action",(evidence[0]["citation"],),"low",False,.99,"planted_unsafe")
        result = evaluate_acceptance_pipeline(rows, HybridRetriever(docs), WrongActionBackend())[0]
        self.assertFalse(result["observed"]["accepted"])
        self.assertIn("action_case_mismatch", result["observed"]["gate_reasons"])


if __name__ == "__main__":
    unittest.main()
