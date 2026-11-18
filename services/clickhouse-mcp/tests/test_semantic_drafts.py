import asyncio
import json
import unittest
from unittest import mock

import semantic
import semantic_drafts as drafts
from tool_errors import GatewayError

TABLES = [
    {"name": "penjualan", "ref": "kai_demo.penjualan", "comment": "Penjualan harian", "total_rows": 3000,
     "sorting_key": "tanggal", "columns": [{"name": "tanggal", "type": "Date", "comment": ""},
                                            {"name": "cabang", "type": "String", "comment": "Cabang"},
                                            {"name": "pendapatan_rp", "type": "UInt64", "comment": "Rupiah"}]},
    {"name": "tiket", "ref": "kai_demo.tiket", "comment": "", "total_rows": 24, "sorting_key": "",
     "columns": [{"name": "tanggal", "type": "Date", "comment": ""}]},
]
REPLY = drafts.Drafts(
    metrics=[
        drafts.MetricDraft(table="kai_demo.penjualan", name="pendapatan_bersih", description="", expression="sum(pendapatan_rp)",
                           metric_type="sum", unit="Rupiah", time_column="tanggal", dimensions=["cabang"], synonyms=["omzet"]),
        drafts.MetricDraft(table="kai_demo.penjualan", name="per_kota", description="", expression="count()",
                           metric_type="count", unit="", time_column="", dimensions=["kota"], synonyms=[]),
    ],
    verified_queries=[drafts.QueryDraft(name="gaji", question="Total gaji", sql="SELECT sum(gaji) FROM hr.gaji",
                                        description="", synonyms=[])],
)


class DraftJobTests(unittest.IsolatedAsyncioTestCase):
    async def run_job(self, reply=REPLY, covered=(), credentials=("budi_demo", "pw"), explain=None):
        saved, explained, audits = [], [], []

        async def creds(_):
            if credentials is None:
                raise GatewayError("AUTH_REQUIRED", "no binding")
            return credentials

        async def ask(group, model):
            self.assertTrue(all(t["ref"] not in covered for t in group))
            return reply

        def query(_overrides, sql, params=None):
            explained.append(sql)
            if explain:
                raise explain
            return []

        patches = [
            mock.patch.object(drafts, "get_managed_credentials", creds),
            mock.patch.object(drafts, "_overrides", lambda user, password: {"username": user}),
            mock.patch.object(drafts, "_catalog", lambda overrides, database: TABLES),
            mock.patch.object(drafts, "_ask_model", ask),
            mock.patch.object(drafts, "_query", query),
            mock.patch.object(semantic, "list_definitions", lambda: [{"tables": list(covered)}] if covered else []),
            mock.patch.object(semantic, "save_draft", lambda cleaned, admin: saved.append((cleaned[1], admin))),
            mock.patch.object(drafts.audit, "info", audits.append),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        drafts.start("kai_demo", "a" * 24)
        await drafts._task
        return drafts._public(), saved, explained, [json.loads(a) for a in audits]

    async def test_valid_drafts_are_explained_and_saved_and_every_skip_has_a_reason(self):
        job, saved, explained, audits = await self.run_job()
        self.assertEqual(job["status"], "done")
        self.assertEqual(saved, [("pendapatan_bersih", "a" * 24)])
        self.assertEqual(explained, ["EXPLAIN SELECT sum(pendapatan_rp) AS pendapatan_bersih FROM kai_demo.penjualan"])
        self.assertEqual((job["drafts_saved"], job["drafts_skipped"], job["tables_done"]), (1, 2, 2))
        reasons = {i["item"]: i["reason"] for i in job["issues"]}
        self.assertIn("kota", reasons["metric per_kota"])
        self.assertIn("di luar database", reasons["verified_query gaji"])
        self.assertEqual((audits[0]["tool"], audits[0]["outcome"], audits[0]["rows"]), ("semantic_draft_job", "ok", 1))

    async def test_tables_with_a_definition_are_skipped_and_clickhouse_rejection_is_reported(self):
        job, saved, _, _ = await self.run_job(covered=("kai_demo.tiket",), explain=RuntimeError("Query execution failed: Unknown function"))
        self.assertEqual((job["tables_existing"], job["groups_total"], saved), (1, 1, []))
        self.assertTrue(any("ClickHouse menolak SQL: Unknown function" == i["reason"] for i in job["issues"]))

    async def test_admin_without_clickhouse_access_gets_a_clear_failure(self):
        job, saved, _, audits = await self.run_job(credentials=None)
        self.assertEqual(job["status"], "failed")
        self.assertIn("Akses data ClickHouse", job["message"])
        self.assertEqual((saved, audits[0]["outcome"]), ([], "error"))

    async def test_a_second_job_is_refused_while_one_runs(self):
        gate = asyncio.Event()

        async def slow(group, model):
            await gate.wait()
            return drafts.Drafts(metrics=[], verified_queries=[])
        with mock.patch.object(drafts, "_ask_model", slow), \
             mock.patch.object(drafts, "get_managed_credentials", mock.AsyncMock(return_value=("u", "p"))), \
             mock.patch.object(drafts, "_overrides", lambda u, p: {}), \
             mock.patch.object(drafts, "_catalog", lambda o, d: TABLES), \
             mock.patch.object(semantic, "list_definitions", lambda: []), \
             mock.patch.object(drafts.audit, "info", lambda line: None):
            drafts.start("kai_demo", "a" * 24)
            await asyncio.sleep(0)
            with self.assertRaises(drafts.SettingsError):
                drafts.start("kai_demo", "a" * 24)
            drafts._cancel.set()
            gate.set()
            await drafts._task
        self.assertEqual(drafts._public()["status"], "cancelled")


class ModelContractTests(unittest.TestCase):
    def test_prompt_carries_schema_only_and_the_schema_bounds_the_reply(self):
        prompt = drafts._prompt(TABLES)
        self.assertIn("Tabel kai_demo.penjualan: Penjualan harian (±3000 baris, ORDER BY tanggal)", prompt)
        self.assertIn("  - pendapatan_rp UInt64: Rupiah", prompt)
        self.assertEqual(drafts.SCHEMA["properties"]["metrics"]["maxItems"], 15)
        with self.assertRaises(Exception):
            drafts.Drafts.model_validate_json('{"metrics": [], "verified_queries": [], "note": "extra"}')


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_is_refused_with_a_clear_message_when_the_model_is_down(self):
        request = mock.Mock(method="POST", headers={"authorization": "Bearer secret"})
        request.body = mock.AsyncMock(return_value=json.dumps({"database": "kai_demo", "updated_by": "a" * 24}).encode())
        with mock.patch.dict("os.environ", {"KAI_PLATFORM_INTERNAL_TOKEN": "secret"}), \
             mock.patch.object(drafts, "model_status", mock.AsyncMock(return_value="unavailable")), \
             mock.patch.object(drafts, "start") as start:
            response = await drafts.drafts_request(request)
        self.assertEqual(response.status_code, 409)
        self.assertIn("make llm-up", json.loads(response.body)["message"])
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
