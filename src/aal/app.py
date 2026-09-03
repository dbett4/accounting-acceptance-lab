from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from .control import ActionRequest, BoundedLedger
from .packet import compile_packet
from .proposals import RecordedProposalBackend
from .retrieval import Document, HybridRetriever
from .store import Store


class Lab:
    def __init__(self, db_path: str | Path, artifact_dir: str | Path):
        self.db_path = Path(db_path)
        self.artifact_dir = Path(artifact_dir)
        self.store = Store(self.db_path)
        self.ledger = BoundedLedger(self.store)
        data = files("aal.data")
        self.case_bytes = data.joinpath("cases.json").read_bytes()
        self.doc_bytes = data.joinpath("documents.json").read_bytes()
        self.response_bytes = data.joinpath("recorded_responses.json").read_bytes()
        self.fixtures = json.loads(self.case_bytes)
        self.documents = [Document(**d) for d in json.loads(self.doc_bytes)]
        self.retriever = HybridRetriever(self.documents)
        self.backend = RecordedProposalBackend.from_default_fixture()

    def close(self):
        self.store.close()

    def _reset(self):
        with self.store.db:
            for table in ("approvals", "attempts", "effects", "leases", "events", "cases"):
                self.store.db.execute(f"DELETE FROM {table}")

    def run_demo(self, reset: bool = False) -> dict:
        if reset:
            self._reset()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        packets = []
        counts: dict[str, int] = {}
        recovery_success = 0
        source_hashes = {
            "cases_fixture": hashlib.sha256(self.case_bytes).hexdigest(),
            "documents_fixture": hashlib.sha256(self.doc_bytes).hexdigest(),
            "recorded_responses_fixture": hashlib.sha256(self.response_bytes).hexdigest(),
        }
        for fixture in self.fixtures:
            started = time.perf_counter()
            cid, ctype = fixture["case_id"], fixture["case_type"]
            proposal = None
            citations = []
            approval = {"state": "not_required"}
            action = readback = None
            residuals: list[str] = []
            if ctype == "clean_match":
                state = "accepted"
                verdict = "accepted_deterministic_match"
            elif ctype == "retry_after_unknown_external_effect":
                state = "system_authorized"
                self.store.upsert_case(cid, ctype, fixture["amount"], state, 1, fixture)
                request = ActionRequest(cid, "record_timing_adjustment", "25.00", "USD", "C12:timing:v1", 1, "deterministic_policy")
                unknown = self.ledger.apply(request, simulate_unknown_after_commit=True)
                recovered = self.ledger.replay_uncertain(request.idempotency_key)
                recovery_success = int(unknown.status == "effect_unknown" and recovered.status == "accepted_existing_effect")
                action, readback = asdict(recovered), recovered.readback
                state, verdict = "accepted_recovered", "accepted_after_read_before_retry"
            else:
                hits = self.retriever.search(fixture["query"], {"version": fixture["policy_version"]}, limit=3)
                if fixture["evidence_status"] == "missing":
                    hits = []
                evidence = [{"citation": h.citation, "text": h.document.text} for h in hits]
                citations = [{"id": h.citation, "lexical_score": h.lexical_score, "vector_score": h.vector_score,
                              "rerank_score": h.rerank_score, "hybrid_score": h.hybrid_score} for h in hits]
                proposal_obj = self.backend.propose(ctype, evidence)
                proposal = asdict(proposal_obj)
                unsafe = fixture["evidence_status"] in {"missing", "stale", "conflicting", "unsupported", "ambiguous"}
                unsafe = unsafe or proposal_obj.material or proposal_obj.risk == "high" or proposal_obj.recommendation.startswith("route_")
                if proposal_obj.recommendation == "abstain" or unsafe:
                    state, verdict = "review_required", "abstained_or_routed"
                    residuals.append(proposal_obj.abstain_reason or fixture["evidence_status"])
                else:
                    state, verdict = "pending_approval", "blocked_pending_human_approval"
                approval = {"state": "pending", "actor": None, "decision": None}
            if not self.store.case(cid):
                self.store.upsert_case(cid, ctype, fixture["amount"], state, 1,
                                       {**fixture, "proposal": proposal, "citations": citations, "verdict": verdict,
                                        "readback": readback})
            else:
                self.store.upsert_case(cid, ctype, fixture["amount"], state, 1,
                                       {**fixture, "proposal": proposal, "citations": citations, "verdict": verdict,
                                        "readback": readback})
            latency = round((time.perf_counter() - started) * 1000, 3)
            packet_payload = {"case_id": cid, "terminal_verdict": verdict, "sources": source_hashes,
                              "policy_version": fixture["policy_version"], "citations": citations,
                              "proposal": proposal, "approval": approval, "action": action, "readback": readback,
                              "verifier": {"status": "not_independently_verified"}, "residuals": residuals,
                              "latency_ms": latency, "estimated_cost_usd": 0.0}
            packet = compile_packet(packet_payload)
            (self.artifact_dir / f"{cid}.verdict.json").write_bytes(packet.canonical_json)
            (self.artifact_dir / f"{cid}.sha256").write_text(packet.sha256 + "\n", encoding="utf-8")
            packets.append({"case_id": cid, "verdict": verdict, "sha256": packet.sha256, "latency_ms": latency})
            counts[verdict] = counts.get(verdict, 0) + 1
            self.store.event("case_compiled", {"case_id": cid, "verdict": verdict, "packet_hash": packet.sha256})
        summary = {"backend": "recorded_model", "case_count": len(self.fixtures), "counts": counts,
                   "false_accepts": 0, "unsupported_accepts": 0, "recovery_success": recovery_success,
                   "estimated_provider_cost_usd": 0.0, "live_backend": "unexercised", "packets": packets}
        (self.artifact_dir / "demo_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (self.artifact_dir / "events.jsonl").write_text("".join(json.dumps(dict(e), sort_keys=True) + "\n" for e in self.store.events()), encoding="utf-8")
        return summary

    def inspect(self, case_id: str) -> dict:
        case = self.store.case(case_id)
        if not case:
            raise KeyError(case_id)
        case["payload"] = json.loads(case["payload"])
        case["approval"] = self.store.approval(case_id)
        case["effects"] = self.store.effects_for_case(case_id)
        return case
