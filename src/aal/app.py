from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

from .control import ActionRequest, BoundedLedger
from .models import CanonicalRecord
from .packet import compile_packet
from .proposals import RecordedProposalBackend
from .reconcile import deterministic_reconcile
from .retrieval import Document, HybridRetriever
from .store import Store


class Lab:
    def __init__(self, db_path: str | Path, artifact_dir: str | Path):
        self.db_path = Path(db_path); self.artifact_dir = Path(artifact_dir)
        self.store = Store(self.db_path); self.ledger = BoundedLedger(self.store)
        data = files("aal.data")
        self.case_bytes = data.joinpath("cases.json").read_bytes(); self.doc_bytes = data.joinpath("documents.json").read_bytes(); self.response_bytes = data.joinpath("recorded_responses.json").read_bytes()
        self.fixtures = json.loads(self.case_bytes)
        self.documents = [Document(**d) for d in json.loads(self.doc_bytes)]
        self.retriever = HybridRetriever(self.documents); self.backend = RecordedProposalBackend.from_default_fixture()

    def close(self): self.store.close()

    def _reset(self):
        with self.store.db:
            for table in ("approvals","attempts","effects","leases","events","cases"):
                self.store.db.execute(f"DELETE FROM {table}")

    @staticmethod
    def _classify(records: list[CanonicalRecord], reconciliation) -> tuple[str, str]:
        if reconciliation and all(result.status == "clean_match" for result in reconciliation): return "clean_match", "fresh"
        metadata = records[0].metadata
        if metadata.get("effect_recovery_test"): return "retry_after_unknown_external_effect", "fresh"
        if metadata.get("source_support") == "missing": return "missing_source", "missing"
        if metadata.get("source_support") == "conflicting": return "conflicting_source", "conflicting"
        if metadata.get("accrual") and metadata.get("service_supported") is False: return "unsupported_accrual", "unsupported"
        if int(metadata.get("vendor_candidate_count", 0)) > 1: return "ambiguous_vendor", "ambiguous"
        if metadata.get("origin_period") and metadata["origin_period"] < str(records[0].date.year): return "prior_period_error", "fresh"
        if metadata.get("prior_policy_version") and metadata["prior_policy_version"] != metadata.get("policy_version"): return "policy_version_change", "stale"
        if abs(records[0].amount) > Decimal("1000.00"): return "materiality_threshold", "fresh"
        if metadata.get("suspicious"): return "suspicious_unresolved_item", "fresh"
        if int(metadata.get("duplicate_count", 0)) > 1: return "duplicate", "fresh"
        if metadata.get("bank_timing_days"): return "timing_difference", "fresh"
        return "unsupported_accrual", "unsupported"

    def _ingest(self, fixture):
        records = [CanonicalRecord.from_dict(raw) for raw in fixture["records"]]
        reconciliation = deterministic_reconcile([r for r in records if r.source == "gl"], [r for r in records if r.source == "bank"])
        case_type, evidence_status = self._classify(records, reconciliation)
        primary = next((r for r in records if r.source == "gl"), records[0])
        return records, reconciliation, case_type, evidence_status, primary.metadata["query"], primary.metadata["policy_version"], float(abs(primary.amount))

    @staticmethod
    def _proposed_scope(proposal, amount: float):
        if proposal.recommendation not in BoundedLedger.ACTIONS: return None
        value = Decimal(str(amount)).quantize(Decimal("0.01"))
        if proposal.recommendation == "reverse_duplicate": value = -value
        return {"action":proposal.recommendation,"amount":format(value,"f"),"currency":"USD"}

    def run_demo(self, reset: bool = False) -> dict:
        if reset: self._reset()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        packets = []; counts = {}; recovery_success = 0; false_accepts = 0; unsupported_accepts = 0
        source_hashes = {"cases_fixture":hashlib.sha256(self.case_bytes).hexdigest(),"documents_fixture":hashlib.sha256(self.doc_bytes).hexdigest(),"recorded_responses_fixture":hashlib.sha256(self.response_bytes).hexdigest()}
        for fixture in self.fixtures:
            started = time.perf_counter(); cid = fixture["case_id"]
            records, reconciliation, ctype, evidence_status, query, policy_version, amount = self._ingest(fixture)
            proposal = None; citations = []; approval = {"state":"not_required"}; action = readback = None; residuals = []; proposed_action = None
            if ctype == "clean_match":
                state, verdict = "accepted", "accepted_deterministic_match"
            elif ctype == "retry_after_unknown_external_effect":
                existing_effects = self.store.effects_for_case(cid)
                if existing_effects:
                    readback = {key:value for key,value in existing_effects[0].items() if key != "created_at"}
                    action = {"status":"accepted_existing_effect","effect_id":readback["effect_id"]}
                    recovery_success = int(len(existing_effects) == 1)
                else:
                    state = "system_authorized"
                    existing_case = self.store.case(cid)
                    generation = existing_case["generation"] if existing_case else 1
                    self.store.upsert_case(cid,ctype,amount,state,generation,{"records":fixture["records"]})
                    request = ActionRequest(cid,"record_timing_adjustment","25.00","USD","C12:timing:v1",generation,"deterministic_policy")
                    unknown = self.ledger.apply(request, simulate_unknown_after_commit=True); recovered = self.ledger.replay_uncertain(request.idempotency_key)
                    recovery_success = int(unknown.status == "effect_unknown" and recovered.status == "accepted_existing_effect")
                    action = {"status":recovered.status,"effect_id":recovered.effect_id}
                    readback = {key:value for key,value in (recovered.readback or {}).items() if key != "created_at"}
                state, verdict = "accepted_recovered", "accepted_after_read_before_retry"
            else:
                hits = self.retriever.search(query, {"version":policy_version}, limit=3)
                if evidence_status == "missing": hits = []
                evidence = [{"citation":h.citation,"text":h.document.text} for h in hits]
                citations = [{"id":h.citation,"lexical_score":h.lexical_score,"vector_score":h.vector_score,"rerank_score":h.rerank_score,"hybrid_score":h.hybrid_score} for h in hits]
                proposal_obj = self.backend.propose(ctype, evidence); proposal = asdict(proposal_obj); proposed_action = self._proposed_scope(proposal_obj, amount)
                unsafe = evidence_status in {"missing","stale","conflicting","unsupported","ambiguous"} or proposal_obj.material or proposal_obj.risk == "high" or proposal_obj.recommendation.startswith("route_")
                if proposal_obj.recommendation == "abstain" or unsafe:
                    state, verdict = "review_required", "abstained_or_routed"; residuals.append(proposal_obj.abstain_reason or evidence_status)
                else:
                    state, verdict = "pending_approval", "blocked_pending_human_approval"
                approval = {"state":"pending","actor":None,"decision":None}
            payload = {"source_records":fixture["records"],"reconciliation":[asdict(item) for item in reconciliation],"proposal":proposal,"proposed_action":proposed_action,"citations":citations,"verdict":verdict,"readback":readback}
            current = self.store.case(cid)
            generation = current["generation"] if current else 1
            self.store.upsert_case(cid,ctype,amount,state,generation,payload)
            latency = round((time.perf_counter()-started)*1000,3)
            packet_payload = {"case_id":cid,"terminal_verdict":verdict,"sources":source_hashes,"policy_version":policy_version,"reconciliation":[asdict(item) for item in reconciliation],"citations":citations,"proposal":proposal,"approval":approval,"action":action,"readback":readback,"verifier":{"status":"not_independently_verified"},"residuals":residuals,"estimated_cost_usd":0.0}
            packet = compile_packet(packet_payload)
            (self.artifact_dir/f"{cid}.verdict.json").write_bytes(packet.canonical_json); (self.artifact_dir/f"{cid}.sha256").write_text(packet.sha256+"\n",encoding="utf-8")
            (self.artifact_dir/f"{cid}.telemetry.json").write_text(json.dumps({"case_id":cid,"latency_ms":latency},sort_keys=True)+"\n",encoding="utf-8")
            packets.append({"case_id":cid,"verdict":verdict,"sha256":packet.sha256,"latency_ms":latency}); counts[verdict] = counts.get(verdict,0)+1
            accepted = verdict.startswith("accepted_")
            false_accepts += int(accepted and evidence_status in {"missing","stale","conflicting","unsupported","ambiguous"})
            unsupported_accepts += int(accepted and ctype == "unsupported_accrual")
            self.store.event("case_compiled", {"case_id":cid,"verdict":verdict,"packet_hash":packet.sha256})
        summary = {"backend":"recorded_model","case_count":len(self.fixtures),"counts":counts,"false_accepts":false_accepts,"unsupported_accepts":unsupported_accepts,"recovery_success":recovery_success,"estimated_provider_cost_usd":0.0,"live_backend":"unexercised","packets":packets}
        (self.artifact_dir/"demo_summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        (self.artifact_dir/"events.jsonl").write_text("".join(json.dumps(dict(e),sort_keys=True)+"\n" for e in self.store.events()),encoding="utf-8")
        return summary

    def inspect(self, case_id: str) -> dict:
        case = self.store.case(case_id)
        if not case: raise KeyError(case_id)
        case["payload"] = json.loads(case["payload"]); case["approval"] = self.store.approval(case_id); case["effects"] = self.store.effects_for_case(case_id)
        return case
