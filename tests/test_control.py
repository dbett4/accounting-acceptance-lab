import json
import tempfile
import threading
import unittest
from pathlib import Path

from aal.control import ActionRequest, ApprovalError, BoundedLedger, GenerationConflict, IdempotencyConflict, OverlapError, proposal_digest
from aal.packet import compile_packet
from aal.store import Store


class ControlPlaneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state.db")
        self.ledger = BoundedLedger(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_proposal_action_is_blocked_without_real_human_approval(self):
        self.store.upsert_case("c1", "duplicate", 100, "pending_approval", 1)
        request = ActionRequest("c1", "reverse_duplicate", "-100.00", "USD", "c1:reverse:v1", 1, "proposal")
        with self.assertRaises(ApprovalError):
            self.ledger.apply(request)
        self.assertEqual(self.store.effects_for_case("c1"), [])

    def test_unknown_effect_is_read_before_retry_and_not_duplicated(self):
        self.store.upsert_case("c2", "retry_after_unknown_external_effect", 25, "system_authorized", 1)
        request = ActionRequest("c2", "record_timing_adjustment", "25.00", "USD", "c2:timing:v1", 1, "deterministic_policy")
        first = self.ledger.apply(request, simulate_unknown_after_commit=True)
        self.assertEqual(first.status, "effect_unknown")
        second = self.ledger.replay_uncertain("c2:timing:v1")
        self.assertEqual(second.status, "accepted_existing_effect")
        self.assertEqual(len(self.store.effects_for_case("c2")), 1)

    def test_idempotency_readback_requires_authority_and_identical_request(self):
        self.store.upsert_case("authorized", "timing_difference", 25, "system_authorized", 1)
        original = ActionRequest("authorized", "record_timing_adjustment", "25.00", "USD", "shared-key", 1, "deterministic_policy")
        self.ledger.apply(original)
        self.store.upsert_case("blocked", "duplicate", 900, "pending_approval", 1)
        substituted = ActionRequest("blocked", "reverse_duplicate", "-900.00", "USD", "shared-key", 1, "proposal", "not-approved")
        with self.assertRaises(ApprovalError): self.ledger.apply(substituted)
        self.assertEqual(self.store.effects_for_case("blocked"), [])
        stale = ActionRequest("authorized", "record_timing_adjustment", "25.00", "USD", "shared-key", 999, "deterministic_policy")
        with self.assertRaises(GenerationConflict): self.ledger.apply(stale)
        changed = ActionRequest("authorized", "record_timing_adjustment", "26.00", "USD", "shared-key", 1, "deterministic_policy")
        with self.assertRaises(IdempotencyConflict): self.ledger.apply(changed)

    def test_human_approval_is_bound_to_proposal_action_amount_and_generation(self):
        proposal = {"recommendation":"reverse_duplicate", "rationale":"confirmed", "citations":["policy:p-duplicate@v2"]}
        scope = {"action":"reverse_duplicate", "amount":"-100.00", "currency":"USD"}
        self.store.upsert_case("approved", "duplicate", 100, "pending_approval", 1, {"proposal":proposal,"proposed_action":scope})
        self.store.approve("approved", "approved", "human-1", "checked")
        digest = proposal_digest(proposal)
        valid = ActionRequest("approved", "reverse_duplicate", "-100.00", "USD", "approved:key", 2, "proposal", digest)
        self.assertEqual(self.ledger.apply(valid).status, "accepted")
        substituted = ActionRequest("approved", "record_timing_adjustment", "1.00", "USD", "approved:other", 2, "proposal", digest)
        with self.assertRaises(ApprovalError): self.ledger.apply(substituted)

    def test_effect_consumes_generation_but_exact_retry_can_read_existing_effect(self):
        self.store.upsert_case("consume", "timing_difference", 25, "system_authorized", 1)
        request = ActionRequest("consume", "record_timing_adjustment", "25.00", "USD", "consume:key", 1, "deterministic_policy")
        self.assertEqual(self.ledger.apply(request).status, "accepted")
        self.assertEqual(self.store.case("consume")["generation"], 2)
        self.assertEqual(self.ledger.apply(request).status, "accepted_existing_effect")
        different_key = ActionRequest("consume", "record_timing_adjustment", "25.00", "USD", "consume:other", 1, "deterministic_policy")
        with self.assertRaises(GenerationConflict): self.ledger.apply(different_key)

    def test_changed_proposal_invalidates_old_approval_and_rejection_is_terminal(self):
        first = {"recommendation":"reverse_duplicate"}; scope = {"action":"reverse_duplicate","amount":"-100.00","currency":"USD"}
        self.store.upsert_case("change", "duplicate", 100, "pending_approval", 1, {"proposal":first,"proposed_action":scope})
        self.store.approve("change", "approved", "human", "first")
        second = {"recommendation":"record_timing_adjustment"}; second_scope = {"action":"record_timing_adjustment","amount":"25.00","currency":"USD"}
        self.store.upsert_case("change", "timing_difference", 25, "pending_approval", 3, {"proposal":second,"proposed_action":second_scope})
        self.assertIsNone(self.store.approval("change"))
        old = ActionRequest("change","reverse_duplicate","-100.00","USD","old",3,"proposal",proposal_digest(first))
        with self.assertRaises(ApprovalError): self.ledger.apply(old)
        self.store.approve("change", "rejected", "human", "no")
        with self.assertRaises(ApprovalError): self.store.approve("change", "approved", "human", "changed mind")

    def test_approval_rejects_scope_that_does_not_match_proposal_recommendation(self):
        proposal = {"recommendation":"reverse_duplicate"}
        wrong_scope = {"action":"record_timing_adjustment","amount":"25.00","currency":"USD"}
        self.store.upsert_case("scope", "duplicate", 100, "pending_approval", 1, {"proposal":proposal,"proposed_action":wrong_scope})
        with self.assertRaises(ApprovalError): self.store.approve("scope", "approved", "human", "wrong")
        wrong_case_proposal = {"recommendation":"record_timing_adjustment"}
        self.store.upsert_case("wrong-case", "duplicate", 100, "pending_approval", 1, {"proposal":wrong_case_proposal,"proposed_action":wrong_scope})
        with self.assertRaises(ApprovalError): self.store.approve("wrong-case", "approved", "human", "wrong case action")

    def test_rejected_case_cannot_be_reopened_by_upsert(self):
        payload = {"proposal":{"recommendation":"reverse_duplicate"},"proposed_action":{"action":"reverse_duplicate","amount":"-100.00","currency":"USD"}}
        self.store.upsert_case("terminal", "duplicate", 100, "pending_approval", 1, payload)
        self.store.approve("terminal", "rejected", "human", "no")
        with self.assertRaises(ApprovalError): self.store.upsert_case("terminal", "duplicate", 100, "pending_approval", 2, payload)

    def test_deterministic_authority_cannot_use_wrong_action_for_case_type(self):
        self.store.upsert_case("wrong-deterministic", "duplicate", 25, "system_authorized", 1)
        request = ActionRequest("wrong-deterministic","record_timing_adjustment","25.00","USD","wrong-det",1,"deterministic_policy")
        with self.assertRaises(ApprovalError): self.ledger.apply(request)

    def test_amount_change_invalidates_approval(self):
        proposal = {"recommendation":"reverse_duplicate"}; scope = {"action":"reverse_duplicate","amount":"-100.00","currency":"USD"}
        payload = {"proposal":proposal,"proposed_action":scope}
        self.store.upsert_case("amount-change","duplicate",100,"pending_approval",1,payload)
        self.store.approve("amount-change","approved","human","ok")
        self.store.upsert_case("amount-change","duplicate",200,"approved",2,payload)
        self.assertIsNone(self.store.approval("amount-change"))

    def test_generation_lease_and_effect_insert_are_atomic_under_concurrency(self):
        db_path = Path(self.tmp.name) / "race.db"
        first = Store(db_path); second = Store(db_path)
        first.upsert_case("race", "timing_difference", 25, "system_authorized", 1)
        barrier = threading.Barrier(2); outcomes = []
        def worker(store, key, owner):
            barrier.wait()
            try:
                outcomes.append(BoundedLedger(store).apply(ActionRequest("race", "record_timing_adjustment", "25.00", "USD", key, 1, "deterministic_policy"), owner=owner).status)
            except (OverlapError, GenerationConflict): outcomes.append("rejected_atomic")
        threads = [threading.Thread(target=worker, args=(first,"race:a","a")), threading.Thread(target=worker, args=(second,"race:b","b"))]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(outcomes), ["accepted", "rejected_atomic"])
        self.assertEqual(len(first.effects_for_case("race")), 1)
        first.close(); second.close()

    def test_generation_and_overlap_guards(self):
        self.store.upsert_case("c3", "timing_difference", 5, "system_authorized", 2)
        stale = ActionRequest("c3", "record_timing_adjustment", "5.00", "USD", "c3:x:v1", 1, "deterministic_policy")
        with self.assertRaises(GenerationConflict):
            self.ledger.apply(stale)
        self.store.acquire_lease("range:ledger", "other-worker", 60)
        current = ActionRequest("c3", "record_timing_adjustment", "5.00", "USD", "c3:x:v2", 2, "deterministic_policy")
        with self.assertRaises(OverlapError):
            self.ledger.apply(current, owner="worker")

    def test_packet_is_byte_identical_for_identical_evidence(self):
        payload = {"case_id":"c4","terminal_verdict":"pending","sources":{"gl":"abc"},"policy_version":"v2","citations":[{"id":"p1","score":0.8}],"proposal":{"provenance":"recorded_model"},"approval":{"state":"pending"},"action":None,"readback":None,"verifier":{"status":"not_run"},"residuals":["human approval"],"latency_ms":3.0,"estimated_cost_usd":0.0}
        a = compile_packet(payload)
        b = compile_packet(json.loads(json.dumps(payload)))
        self.assertEqual(a.canonical_json, b.canonical_json)
        self.assertEqual(a.sha256, b.sha256)
        without_verdict = dict(payload); without_verdict.pop("terminal_verdict")
        with self.assertRaises(ValueError): compile_packet(without_verdict)


if __name__ == "__main__":
    unittest.main()
