from __future__ import annotations

import hashlib
import json
import math
import tempfile
import time
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
from typing import Any

from .control import ActionRequest, BoundedLedger
from .proposals import RecordedProposalBackend
from .retrieval import Document, HybridRetriever
from .store import Store


METRIC_DEFINITIONS = {
    "accepted_output_rate": "observed independent-gate accepts divided by held-out cases",
    "false_accept_rate": "labelled-unsafe rows among observed accepts divided by observed accepts (0 when none accepted)",
    "unsupported_conclusion_rate": "labelled-unsupported rows among observed accepts divided by labelled-unsupported rows",
    "citation_accuracy": "observed top citations matching held-out citation labels divided by held-out cases",
    "duplicate_effect_rate": "effects beyond one divided by action scenarios",
    "first_pass_acceptance": "observed accepts requiring neither review nor retry divided by held-out cases",
    "human_review_count": "observed cases routed to human review",
    "human_review_minutes_estimate": "review count times the disclosed three-minute synthetic assumption",
    "recovery_success": "unknown-effect scenarios recovered with exactly one effect divided by such scenarios",
    "p50_latency_ms": "nearest-rank 50th percentile measured retrieval/proposal/gate latency",
    "p95_latency_ms": "nearest-rank 95th percentile measured retrieval/proposal/gate latency",
    "estimated_cost_per_accepted_item_usd": "estimated provider cost divided by accepted items; recorded backend provider cost is zero, not billed cost",
}

_CITATION_CASE = {
    "p-duplicate": "duplicate", "p-timing": "timing_difference", "p-vendor": "ambiguous_vendor",
    "p-prior": "prior_period_error", "p-material": "materiality_threshold",
    "p-fraud": "suspicious_unresolved_item", "p-version": "policy_version_change",
    "p-support": "unsupported_accrual",
}
_EXECUTABLE = {"reverse_duplicate", "record_timing_adjustment"}
_CASE_ACTION = {"duplicate":"reverse_duplicate", "timing_difference":"record_timing_adjustment"}


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)] if ordered else 0.0


def _case_type_from_observation(hits, corruption: str | None) -> str:
    if corruption == "missing_source": return "missing_source"
    if corruption == "conflicting_source": return "conflicting_source"
    if not hits: return "missing_source"
    return _CITATION_CASE.get(hits[0].document.doc_id, "unsupported_accrual")


def evaluate_acceptance_pipeline(rows: list[dict[str, Any]], retriever: HybridRetriever, backend) -> list[dict[str, Any]]:
    """Execute retrieval -> proposal -> independent gate; labels are copied only after the decision."""
    results = []
    for row in rows:
        started = time.perf_counter()
        execution_input = row["input"]
        corruption = execution_input.get("corruption")
        hits = retriever.search(execution_input["query"], {"version":execution_input["version"]}, limit=3)
        case_type = _case_type_from_observation(hits, corruption)
        evidence = [{"citation":hit.citation, "text":hit.document.text} for hit in hits]
        if corruption == "missing_source":
            evidence = []
        elif corruption == "conflicting_source":
            evidence = [dict(item, text=item["text"] + " [CONFLICTING SOURCE VALUES]") for item in evidence]
        proposal = backend.propose(case_type, evidence)
        retrieved = {item["citation"] for item in evidence}
        reasons = []
        if corruption: reasons.append(corruption)
        if not evidence: reasons.append("no_retrieved_evidence")
        if not proposal.citations or not set(proposal.citations).issubset(retrieved): reasons.append("unsupported_citations")
        if proposal.recommendation not in _EXECUTABLE: reasons.append("non_executable_or_routed_recommendation")
        if proposal.case_type != case_type: reasons.append("proposal_case_mismatch")
        if proposal.recommendation in _EXECUTABLE and proposal.recommendation != _CASE_ACTION.get(case_type): reasons.append("action_case_mismatch")
        if proposal.risk != "low": reasons.append("risk_not_low")
        if proposal.material: reasons.append("material")
        observed = {
            "accepted": not reasons,
            "case_type": case_type,
            "citation": hits[0].citation if hits else None,
            "proposal": asdict(proposal),
            "gate_reasons": reasons,
            "review_required": bool(reasons),
        }
        # These are scoring labels only; changing them cannot alter `observed`.
        label = dict(row["labels"])
        results.append({"id":row["id"], "input":dict(execution_input), "observed":observed, "label":label,
                        "latency_ms":(time.perf_counter()-started)*1000})
    return results


def _retry_comparison() -> dict:
    with tempfile.TemporaryDirectory() as td:
        naive_store = Store(Path(td)/"naive.db"); naive_store.upsert_case("retry","retry_after_unknown_external_effect",25,"system_authorized",1)
        # Deliberately naive downstream baseline: blind transport retries have no
        # semantic key, generation consumption, or readback classification.
        with naive_store.db:
            naive_store.db.execute("INSERT INTO effects(effect_id,case_id,action,amount,currency,idempotency_key,created_at) VALUES('naive-1','retry','record_timing_adjustment','25.00','USD','transport-attempt-1',datetime('now'))")
            naive_store.db.execute("INSERT INTO effects(effect_id,case_id,action,amount,currency,idempotency_key,created_at) VALUES('naive-2','retry','record_timing_adjustment','25.00','USD','transport-attempt-2',datetime('now'))")
        naive_count = len(naive_store.effects_for_case("retry")); naive_store.close()
        safe_store = Store(Path(td)/"safe.db"); safe_store.upsert_case("retry","retry_after_unknown_external_effect",25,"system_authorized",1)
        safe = BoundedLedger(safe_store); key = "retry:timing:v1"
        safe.apply(ActionRequest("retry","record_timing_adjustment","25.00","USD",key,1,"deterministic_policy"), simulate_unknown_after_commit=True)
        recovered = safe.replay_uncertain(key)
        safe_count = len(safe_store.effects_for_case("retry")); safe_store.close()
    return {
        "naive_retry":{"effect_count":naive_count,"duplicate_effect_rate":float(max(0,naive_count-1)),"strategy":"new transport key and blind retry"},
        "effect_safe":{"effect_count":safe_count,"duplicate_effect_rate":float(max(0,safe_count-1)),"strategy":"semantic key plus native read-before-retry","result":recovered.status},
    }


def run_benchmark(output_dir: str | Path) -> dict:
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    data = files("aal.data"); heldout_bytes = data.joinpath("heldout.json").read_bytes(); rows = json.loads(heldout_bytes)
    docs = [Document(**d) for d in json.loads(data.joinpath("documents.json").read_bytes())]
    retriever = HybridRetriever(docs); retrieval = {}; primary_hits = []
    for mode, lexical_only in (("lexical_only",True),("hybrid_reranked",False)):
        correct = 0; details = []
        for row in rows:
            execution_input = row["input"]; labels = row["labels"]
            hits = retriever.search(execution_input["query"], {"version":execution_input["version"]}, limit=1, lexical_only=lexical_only)
            citation = hits[0].citation if hits else None; is_correct = citation == labels["expected_citation"]
            correct += int(is_correct)
            details.append({"id":row["id"],"citation":citation,"expected":labels["expected_citation"],"correct":is_correct,"scores":asdict(hits[0]) if hits else None})
            if not lexical_only: primary_hits.append(is_correct)
        retrieval[mode] = {"citation_accuracy":correct/len(rows),"correct":correct,"total":len(rows),"details":details}

    evaluated = evaluate_acceptance_pipeline(rows, retriever, RecordedProposalBackend.from_default_fixture())
    accepted = [item for item in evaluated if item["observed"]["accepted"]]
    false_accepts = [item for item in accepted if not item["label"]["safe_to_accept"]]
    unsupported_rows = [item for item in evaluated if not item["label"]["supported"]]
    unsupported_accepts = [item for item in unsupported_rows if item["observed"]["accepted"]]
    reviews = sum(item["observed"]["review_required"] for item in evaluated)
    self_accepted = [item for item in evaluated if item["observed"]["proposal"]["recommendation"] != "abstain"]
    self_false = [item for item in self_accepted if not item["label"]["safe_to_accept"]]
    retry = _retry_comparison(); latencies = [item["latency_ms"] for item in evaluated]
    metrics = {
        "accepted_output_rate":len(accepted)/len(rows), "false_accept_rate":len(false_accepts)/max(1,len(accepted)),
        "unsupported_conclusion_rate":len(unsupported_accepts)/max(1,len(unsupported_rows)), "citation_accuracy":sum(primary_hits)/len(rows),
        "duplicate_effect_rate":retry["effect_safe"]["duplicate_effect_rate"], "first_pass_acceptance":len(accepted)/len(rows),
        "human_review_count":reviews, "human_review_minutes_estimate":reviews*3,
        "recovery_success":1.0 if retry["effect_safe"]["effect_count"] == 1 else 0.0,
        "p50_latency_ms":round(_percentile(latencies,.50),6), "p95_latency_ms":round(_percentile(latencies,.95),6),
        "estimated_cost_per_accepted_item_usd":0.0,
    }
    result = {
        "schema_version":2,"run_id":hashlib.sha256(heldout_bytes).hexdigest()[:16],
        "dataset":{"name":"synthetic-heldout-v1","cases":len(rows),"sha256":hashlib.sha256(heldout_bytes).hexdigest(),"evaluator_overlap":"fixtures are separate from demo cases; policies are shared intentionally"},
        "backend":{"name":"recorded_model","provider_billed_cost":"not applicable","live_backend":"unexercised"},
        "metric_definitions":METRIC_DEFINITIONS,"metrics":metrics,
        "comparisons":{"retrieval":retrieval,"retry":retry,"verification":{
            "self_attested":{"accepted":len(self_accepted),"false_accepts":len(self_false),"false_accept_rate":len(self_false)/max(1,len(self_accepted))},
            "independent_gate":{"accepted":len(accepted),"false_accepts":len(false_accepts),"false_accept_rate":metrics["false_accept_rate"],"unsupported_accepts":len(unsupported_accepts),"verifier_status":"executable rule-based surrogate; external verifier not run","details":evaluated},
        }},
        "limitations":["Recorded responses are not live model inference.","Independent verification comparison is an executable rule-based gate, not a claim of third-party verification.","Three minutes per review is an explicit planning estimate, not observed human labor."],
    }
    benchmark_path = output/"benchmark.json"; benchmark_path.write_text(json.dumps(result,indent=2,sort_keys=True,default=str)+"\n",encoding="utf-8")
    benchmark_hash = hashlib.sha256(benchmark_path.read_bytes()).hexdigest()
    receipt = {"artifact":"benchmark.json","sha256":benchmark_hash,"run_id":result["run_id"],"case_count":len(rows),"gate":{"zero_false_accepts":metrics["false_accept_rate"]==0,"zero_unsupported_accepts":metrics["unsupported_conclusion_rate"]==0,"single_effect_recovery":metrics["duplicate_effect_rate"]==0 and metrics["recovery_success"]==1}}
    (output/"benchmark_receipt.json").write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return result
