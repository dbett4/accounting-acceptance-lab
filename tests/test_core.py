import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from aal.models import CanonicalRecord, ValidationError
from aal.reconcile import deterministic_reconcile
from aal.retrieval import Document, HybridRetriever
from aal.proposals import OpenAICompatibleBackend, RecordedProposalBackend


class CoreTest(unittest.TestCase):
    def test_typed_records_and_deterministic_matching(self):
        gl = CanonicalRecord.from_dict({"record_id":"g1","source":"gl","amount":"125.00","date":"2026-01-02","vendor":"Acme","reference":"INV-1"})
        bank = CanonicalRecord.from_dict({"record_id":"b1","source":"bank","amount":"-125.00","date":"2026-01-02","vendor":"ACME","reference":"INV-1"})
        result = deterministic_reconcile([gl], [bank])
        self.assertEqual(result[0].status, "clean_match")
        self.assertEqual(result[0].matched_ids, ("g1", "b1"))
        with self.assertRaises(ValidationError):
            CanonicalRecord.from_dict({"record_id":"x","source":"email","amount":"nope","date":"bad"})

    def test_hybrid_retrieval_filters_expands_and_cites(self):
        docs = [
            Document("p1", "policy", "v2", "2026-01-01", "Duplicate payments", "Duplicate payments must be reversed after invoice and amount confirmation.", {"kind":"policy"}),
            Document("p1-c1", "policy", "v2", "2026-01-01", "Duplicate child", "same invoice paid twice reversal", {"kind":"policy"}, parent_id="p1"),
            Document("old", "policy", "v1", "2024-01-01", "Old", "duplicates can be ignored", {"kind":"policy"}),
        ]
        hits = HybridRetriever(docs).search("duplicate invoice reversal", filters={"version":"v2"}, limit=2)
        self.assertEqual(hits[0].document.doc_id, "p1")
        self.assertGreater(hits[0].hybrid_score, 0)
        self.assertIn("p1", hits[0].citation)
        self.assertTrue(all(h.document.version == "v2" for h in hits))

    def test_recorded_backend_is_structured_and_provenanced(self):
        backend = RecordedProposalBackend.from_default_fixture()
        proposal = backend.propose("duplicate", [{"citation":"policy:p-duplicate@v2","text":"reverse duplicate"}])
        self.assertEqual(proposal.provenance, "recorded_model")
        self.assertEqual(proposal.case_type, "duplicate")
        self.assertTrue(proposal.citations)
        with self.assertRaises(ValidationError):
            backend.validate({"case_type":"duplicate", "recommendation":"post"})

    def _serve_model_response(self, content):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                requests.append({"path": self.path, "headers": dict(self.headers),
                                 "body": json.loads(self.rfile.read(length))})
                body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_):
                return
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}", requests

    def test_openai_compatible_backend_uses_local_protocol_and_validates_output(self):
        content = json.dumps({"case_type":"duplicate", "recommendation":"route_review",
                              "rationale":"duplicate evidence", "citations":["policy:p1@v2"],
                              "risk":"medium", "material":False, "confidence":0.8})
        base_url, requests = self._serve_model_response(content)
        env = {"AAL_OPENAI_BASE_URL":base_url, "AAL_OPENAI_API_KEY":"local-test-value",
               "AAL_OPENAI_MODEL":"fake-model"}
        with patch.dict(os.environ, env, clear=False):
            proposal = OpenAICompatibleBackend().propose(
                "duplicate", [{"citation":"policy:p1@v2", "text":"duplicate evidence"}])
        self.assertEqual(proposal.provenance, "live_model:fake-model")
        self.assertEqual(requests[0]["path"], "/chat/completions")
        self.assertEqual(requests[0]["body"]["response_format"], {"type":"json_object"})

    def test_openai_compatible_backend_rejects_malformed_structured_output(self):
        base_url, _ = self._serve_model_response(json.dumps({"recommendation":"post"}))
        with patch.dict(os.environ, {"AAL_OPENAI_BASE_URL":base_url,
                                     "AAL_OPENAI_API_KEY":"local-test-value",
                                     "AAL_OPENAI_MODEL":"fake-model"}, clear=False):
            with self.assertRaises(ValidationError):
                OpenAICompatibleBackend().propose("duplicate", [])

    def test_openai_compatible_backend_rejects_unretrieved_citation(self):
        content = json.dumps({"case_type":"duplicate", "recommendation":"post",
                              "rationale":"looks supported", "citations":["policy:invented@v9"],
                              "risk":"low", "material":False, "confidence":0.9})
        base_url, _ = self._serve_model_response(content)
        with patch.dict(os.environ, {"AAL_OPENAI_BASE_URL":base_url,
                                     "AAL_OPENAI_API_KEY":"local-test-value",
                                     "AAL_OPENAI_MODEL":"fake-model"}, clear=False):
            with self.assertRaises(ValidationError):
                OpenAICompatibleBackend().propose(
                    "duplicate", [{"citation":"policy:p1@v2", "text":"real evidence"}])


if __name__ == "__main__":
    unittest.main()
