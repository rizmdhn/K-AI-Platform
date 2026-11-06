"""Offline predicate probe of the digest-verified candidate source; no HTTP/DB/provider test."""

import hashlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

EXPECTED = "c307b602fdb7c2191966d0d1aecb51788ced35e23d84f0db11fd6f3289e26b7c"


class CandidateScopeTests(unittest.TestCase):
    def request(self, user):
        return SimpleNamespace(state=SimpleNamespace(user=user))

    def test_owner_is_part_of_file_predicate_before_store_query(self):
        scope = candidate.resolve_scope(self.request({"id": "alice"}))
        predicate = scope.predicate(candidate.file_clause("file-1"))
        self.assertEqual(
            predicate,
            {"$and": [{"file_id": {"$eq": "file-1"}}, {"user_id": {"$in": ["alice"]}}]},
        )
        self.assertFalse(scope.owns("bob"))
        self.assertFalse(scope.owns(None))

    def test_caller_asserted_entity_expands_scope_without_membership_proof(self):
        scope = candidate.resolve_scope(self.request({"id": "alice"}), entity_id="bob")
        self.assertEqual(scope.owners, ("alice", "bob"))
        self.assertTrue(scope.owns("bob"))

    def test_absent_verified_payload_falls_back_to_public_or_asserted_entity(self):
        self.assertEqual(
            candidate.resolve_scope(self.request(None)).owners, ("public",)
        )
        self.assertEqual(
            candidate.resolve_scope(self.request(None), entity_id="bob").owners,
            ("bob",),
        )

    def test_payload_without_id_is_not_rejected_here(self):
        self.assertEqual(candidate.resolve_scope(self.request({})).owners, ("public",))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python3 tests/probe_rag_scope.py /path/to/verified/app/scope.py"
        )
    source = Path(sys.argv[1])
    if hashlib.sha256(source.read_bytes()).hexdigest() != EXPECTED:
        raise SystemExit("Candidate source digest mismatch; probe refused.")
    # Request is used only as a type annotation; this does not emulate FastAPI authentication.
    sys.modules["fastapi"] = SimpleNamespace(Request=object)
    spec = importlib.util.spec_from_file_location("candidate_scope", source)
    candidate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = candidate
    spec.loader.exec_module(candidate)
    unittest.main(argv=[sys.argv[0]], verbosity=2)
