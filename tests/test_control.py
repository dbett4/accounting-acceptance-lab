import json
import tempfile
import unittest
from pathlib import Path

from aal.control import ActionRequest, ApprovalError, BoundedLedger, GenerationConflict, OverlapError
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
        payload = {"case_id":"c4","sources":{"gl":"abc"},"policy_version":"v2","citations":[{"id":"p1","score":0.8}],"proposal":{"provenance":"recorded_model"},"approval":{"state":"pending"},"action":None,"readback":None,"verifier":{"status":"not_run"},"residuals":["human approval"],"latency_ms":3.0,"estimated_cost_usd":0.0}
        a = compile_packet(payload)
        b = compile_packet(json.loads(json.dumps(payload)))
        self.assertEqual(a.canonical_json, b.canonical_json)
        self.assertEqual(a.sha256, b.sha256)


if __name__ == "__main__":
    unittest.main()
