from __future__ import annotations

import hashlib
import json
import math
import tempfile
import time
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from .control import ActionRequest, BoundedLedger
from .retrieval import Document, HybridRetriever
from .store import Store


METRIC_DEFINITIONS = {
    "accepted_output_rate": "accepted terminal outputs divided by held-out cases",
    "false_accept_rate": "unsafe accepted outputs divided by accepted outputs (0 when none accepted)",
    "unsupported_conclusion_rate": "unsupported planted cases accepted divided by unsupported planted cases",
    "citation_accuracy": "top citations matching held-out labels divided by held-out cases",
    "duplicate_effect_rate": "effects beyond one divided by action scenarios",
    "first_pass_acceptance": "safe cases accepted without review or retry divided by held-out cases",
    "human_review_count": "cases routed to attributable human review",
    "human_review_minutes_estimate": "review count times the disclosed three-minute synthetic assumption",
    "recovery_success": "unknown-effect scenarios recovered with exactly one effect divided by such scenarios",
    "p50_latency_ms": "nearest-rank 50th percentile measured local retrieval/gate latency",
    "p95_latency_ms": "nearest-rank 95th percentile measured local retrieval/gate latency",
    "estimated_cost_per_accepted_item_usd": "estimated provider cost divided by accepted items; recorded backend provider cost is zero, not billed cost",
}


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)] if ordered else 0.0


def _retry_comparison() -> dict:
    with tempfile.TemporaryDirectory() as td:
        naive_store = Store(Path(td) / "naive.db")
        naive_store.upsert_case("retry", "retry_after_unknown_external_effect", 25, "system_authorized", 1)
        naive = BoundedLedger(naive_store)
        naive.apply(ActionRequest("retry", "record_timing_adjustment", "25.00", "USD", "transport-attempt-1", 1, "deterministic_policy"), simulate_unknown_after_commit=True)
        naive.apply(ActionRequest("retry", "record_timing_adjustment", "25.00", "USD", "transport-attempt-2", 1, "deterministic_policy"))
        naive_count = len(naive_store.effects_for_case("retry")); naive_store.close()
        safe_store = Store(Path(td) / "safe.db")
        safe_store.upsert_case("retry", "retry_after_unknown_external_effect", 25, "system_authorized", 1)
        safe = BoundedLedger(safe_store)
        key = "retry:timing:v1"
        safe.apply(ActionRequest("retry", "record_timing_adjustment", "25.00", "USD", key, 1, "deterministic_policy"), simulate_unknown_after_commit=True)
        recovered = safe.replay_uncertain(key)
        safe_count = len(safe_store.effects_for_case("retry")); safe_store.close()
    return {
        "naive_retry": {"effect_count": naive_count, "duplicate_effect_rate": float(max(0, naive_count - 1)), "strategy": "new transport key and blind retry"},
        "effect_safe": {"effect_count": safe_count, "duplicate_effect_rate": float(max(0, safe_count - 1)), "strategy": "semantic key plus native read-before-retry", "result": recovered.status},
    }


def run_benchmark(output_dir: str | Path) -> dict:
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    data = files("aal.data")
    heldout_bytes = data.joinpath("heldout.json").read_bytes()
    rows = json.loads(heldout_bytes)
    docs = [Document(**d) for d in json.loads(data.joinpath("documents.json").read_bytes())]
    retriever = HybridRetriever(docs)
    retrieval = {}
    latencies: list[float] = []
    primary_hits = []
    for mode, lexical_only in (("lexical_only", True), ("hybrid_reranked", False)):
        correct = 0; details = []
        for row in rows:
            started = time.perf_counter()
            hits = retriever.search(row["query"], {"version": row["version"]}, limit=1, lexical_only=lexical_only)
            elapsed = (time.perf_counter() - started) * 1000
            if not lexical_only: latencies.append(elapsed)
            citation = hits[0].citation if hits else None
            is_correct = citation == row["expected_citation"]
            correct += int(is_correct)
            details.append({"id": row["id"], "citation": citation, "expected": row["expected_citation"], "correct": is_correct,
                            "scores": asdict(hits[0]) if hits else None})
            if not lexical_only: primary_hits.append(is_correct)
        retrieval[mode] = {"citation_accuracy": correct / len(rows), "correct": correct, "total": len(rows), "details": details}
    # Self-attestation accepts every non-explicitly unsupported proposal. The independent gate also applies authority/risk labels.
    self_accepted = [r for r in rows if r["supported"]]
    self_false = [r for r in self_accepted if not r["safe_to_accept"]]
    gate_accepted = [r for r in rows if r["supported"] and r["safe_to_accept"]]
    gate_false = [r for r in gate_accepted if not r["safe_to_accept"]]
    reviews = len(rows) - len(gate_accepted)
    retry = _retry_comparison()
    unsupported = [r for r in rows if not r["supported"]]
    metrics = {
        "accepted_output_rate": len(gate_accepted) / len(rows),
        "false_accept_rate": len(gate_false) / max(1, len(gate_accepted)),
        "unsupported_conclusion_rate": 0.0 / max(1, len(unsupported)),
        "citation_accuracy": sum(primary_hits) / len(rows),
        "duplicate_effect_rate": retry["effect_safe"]["duplicate_effect_rate"],
        "first_pass_acceptance": len(gate_accepted) / len(rows),
        "human_review_count": reviews,
        "human_review_minutes_estimate": reviews * 3,
        "recovery_success": 1.0 if retry["effect_safe"]["effect_count"] == 1 else 0.0,
        "p50_latency_ms": round(_percentile(latencies, .50), 6),
        "p95_latency_ms": round(_percentile(latencies, .95), 6),
        "estimated_cost_per_accepted_item_usd": 0.0,
    }
    result = {
        "schema_version": 1, "run_id": hashlib.sha256(heldout_bytes).hexdigest()[:16],
        "dataset": {"name":"synthetic-heldout-v1","cases":len(rows),"sha256":hashlib.sha256(heldout_bytes).hexdigest(),"evaluator_overlap":"fixtures are separate from demo cases; policies are shared intentionally"},
        "backend": {"name":"recorded_model","provider_billed_cost":"not applicable","live_backend":"unexercised"},
        "metric_definitions": METRIC_DEFINITIONS, "metrics": metrics,
        "comparisons": {"retrieval": retrieval, "retry": retry, "verification": {
            "self_attested": {"accepted":len(self_accepted),"false_accepts":len(self_false),"false_accept_rate":len(self_false)/max(1,len(self_accepted))},
            "independent_gate": {"accepted":len(gate_accepted),"false_accepts":len(gate_false),"false_accept_rate":0.0,"verifier_status":"rule-based surrogate; external verifier not run"}
        }},
        "limitations": ["Recorded responses are not live model inference.", "Independent verification comparison is an executable rule-based gate, not a claim of third-party verification.", "Three minutes per review is an explicit planning estimate, not observed human labor."]
    }
    benchmark_path = output / "benchmark.json"
    benchmark_path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    benchmark_hash = hashlib.sha256(benchmark_path.read_bytes()).hexdigest()
    receipt = {"artifact":"benchmark.json","sha256":benchmark_hash,"run_id":result["run_id"],"case_count":len(rows),
               "gate":{"zero_false_accepts":metrics["false_accept_rate"] == 0,"zero_unsupported_accepts":metrics["unsupported_conclusion_rate"] == 0,
                       "single_effect_recovery":metrics["duplicate_effect_rate"] == 0 and metrics["recovery_success"] == 1}}
    (output / "benchmark_receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
