import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("eval_sets", ROOT / "evals/validate.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


class EvaluationSetTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = json.loads((ROOT / "evals/fixtures.json").read_text())
        self.cases = json.loads((ROOT / "evals/cases.json").read_text())

    def test_corpus_is_valid_and_oracle_enforces_synthetic_partition(self):
        report = ev.validate(self.fixtures, self.cases)
        self.assertGreaterEqual(report["cases"], 18)
        self.assertEqual(set(report["domains"]), {"sql", "metadata", "documents"})
        sql = "SELECT sum(net_revenue_cents) FROM sales.orders WHERE status='completed' AND order_date>='2026-01-01' AND order_date<'2026-02-01'"
        self.assertEqual(ev.oracle(self.fixtures, "sales_id", sql), [[35000]])
        self.assertEqual(ev.oracle(self.fixtures, "sales_all", sql), [[75000]])

    def test_export_keeps_goldens_rows_and_private_revoked_superseded_evidence_out(
        self,
    ):
        case = next(c for c in self.cases["cases"] if c["id"] == "docs-current-policy")
        output = ev.context(self.fixtures, case)
        self.assertEqual(output["question"], case["question"])
        self.assertNotIn("expected", output)
        self.assertNotIn("reference_sql", json.dumps(output))
        self.assertNotIn("EVAL_FINANCE_CANARY", json.dumps(output))
        self.assertNotIn("EVAL_SG_CANARY", json.dumps(output))
        ids = [d["id"] for d in output["documents"]]
        self.assertIn("sales-policy-v2", ids)
        for hidden in [
            "finance-budget",
            "sg-forecast",
            "sales-policy-v1",
            "revoked-policy",
            "policy-duplicate",
        ]:
            self.assertNotIn(hidden, ids)
        self.assertTrue(all(d["untrusted"] for d in output["documents"]))
        self.assertNotIn("rows", json.dumps(output["schema"]))

    def test_ai_function_cases_label_exactly_the_rows_the_persona_can_read(self):
        case = next(c for c in self.cases["cases"] if c["id"] == "af-sentiment-tickets")
        self.assertEqual(sorted(case["expected"]["labels"].values()).count("negatif"), 3)
        self.assertNotIn("7", case["expected"]["labels"])  # SG ticket is outside support_id's rows
        broken = copy.deepcopy(self.cases)
        next(c for c in broken["cases"] if c["id"] == "af-sentiment-tickets")["expected"]["labels"]["7"] = "negatif"
        with self.assertRaises(ValueError):
            ev.validate(self.fixtures, broken)

    def test_rejects_duplicate_ids_wrong_results_and_unauthorized_evidence(self):
        duplicate = copy.deepcopy(self.cases)
        duplicate["cases"].append(copy.deepcopy(duplicate["cases"][0]))
        with self.assertRaises(ValueError):
            ev.validate(self.fixtures, duplicate)
        wrong = copy.deepcopy(self.cases)
        wrong["cases"][0]["expected"]["rows"] = [[999]]
        with self.assertRaises(ValueError):
            ev.validate(self.fixtures, wrong)
        denied = copy.deepcopy(self.cases)
        case = next(c for c in denied["cases"] if c["id"] == "docs-current-policy")
        case["expected"]["citations"] = ["finance-budget"]
        with self.assertRaises(ValueError):
            ev.validate(self.fixtures, denied)

    def test_reference_oracle_cannot_write_or_read_another_partition(self):
        for sql in [
            "DROP TABLE sales.orders",
            "ATTACH DATABASE '/tmp/outside.sqlite' AS external",
            "SELECT * FROM finance.salaries",
            "SELECT load_extension('anything')",
        ]:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                ev.oracle(self.fixtures, "sales_id", sql)

    def test_rejects_unreviewed_relationships_and_multiple_current_source_versions(
        self,
    ):
        case = next(c for c in self.cases["cases"] if c["id"] == "docs-current-policy")
        relationship = copy.deepcopy(self.fixtures)
        relationship["tables"]["sales.orders"]["relationships"] = [
            {"table": "finance.salaries", "detail": "EVAL_RELATION_PRIVATE_CANARY"}
        ]
        stale = copy.deepcopy(self.fixtures)
        next(d for d in stale["documents"] if d["id"] == "sales-policy-v1")[
            "status"
        ] = "active"
        for changed in [relationship, stale]:
            with self.subTest(changed=changed is relationship):
                with self.assertRaises(ValueError):
                    ev.validate(changed, self.cases)
                with self.assertRaises(ValueError):
                    ev.context(changed, case)
