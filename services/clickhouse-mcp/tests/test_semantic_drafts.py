import asyncio
import json
from datetime import datetime, timedelta, timezone
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
    async def run_job(self, reply=REPLY, covered=(), credentials=("budi_demo", "pw"), explain=None, databases=("kai_demo",)):
        databases = list(databases)
        saved, explained, audits = [], [], []

        async def creds(_):
            if credentials is None:
                raise GatewayError("AUTH_REQUIRED", "no binding")
            return credentials

        async def ask(group, model):
            self.assertTrue(all(t["ref"] not in covered for t in group))
            return reply

        def query(_overrides, sql, params=None):
            if sql.startswith("SELECT database, count()"):
                return [{"database": d, "n": len(TABLES)} for d in params["dbs"]]
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
            mock.patch.object(semantic, "covered_tables", lambda env=None, database=None: {ref: {} for ref in covered}),
            mock.patch.object(semantic, "save_draft", lambda cleaned, admin: saved.append((cleaned[1], admin))),
            mock.patch.object(drafts.audit, "info", audits.append),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        drafts.start(databases, "a" * 24)
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
        self.assertEqual((job["queue"][0]["tables_existing"], job["tables_total"], saved), (1, 1, []))
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
             mock.patch.object(drafts, "_query", lambda o, sql, params=None: [{"database": "kai_demo", "n": 2}]), \
             mock.patch.object(semantic, "covered_tables", lambda env=None, database=None: {}), \
             mock.patch.object(drafts.audit, "info", lambda line: None):
            drafts.start(["kai_demo", "kai_lain"], "a" * 24)
            await asyncio.sleep(0)
            with self.assertRaises(drafts.SettingsError):
                drafts.start(["kai_demo"], "a" * 24)
            drafts._cancel.set()
            gate.set()
            await drafts._task
        job = drafts._public()
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual([e["status"] for e in job["queue"]], ["cancelled", "cancelled"])  # nothing left half-marked running

    async def test_queue_runs_databases_in_order_with_per_table_speed_and_eta(self):
        job, saved, _, audits = await self.run_job(databases=("kai_demo", "kai_dua"))
        self.assertEqual([e["status"] for e in job["queue"]], ["done", "done"])
        self.assertEqual((job["tables_total"], job["tables_done"]), (4, 4))
        self.assertIsNotNone(job["seconds_per_table"])
        self.assertIsNone(job["eta_seconds"])  # finished: no ETA
        self.assertEqual(audits[0]["database"], "kai_demo,kai_dua")

    def test_eta_counts_down_remaining_tables_minus_the_running_group(self):
        drafts._job.clear()
        self.addCleanup(drafts._job.clear)
        drafts._job.update(status="running", queue=[], tables_total=10, tables_done=4, seconds_per_table=60.0, model_seconds=240.0,
                           group_started_at=(datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat())
        eta = drafts._public()["eta_seconds"]
        self.assertTrue(325 <= eta <= 330, eta)  # 6 tables x 60 s, minus ~30 s already spent

    def test_queue_input_is_validated(self):
        for body in ({"databases": []}, {"databases": ["a"] * 2}, {"databases": ["system"]}, {"databases": ["x;y"]},
                     {"databases": [f"d{i}" for i in range(21)]}, {}):
            with self.assertRaises(drafts.SettingsError):
                drafts._databases(body)
        self.assertEqual(drafts._databases({"databases": ["a", "b"]}), ["a", "b"])


class TablePageTests(unittest.TestCase):
    TABLES = [{"name": n, "ref": f"db.{n}", "comment": c, "total_rows": r} for n, c, r in
              (("penjualan", "transaksi", 900), ("cabang", "", 4), ("tiket", "keluhan", 50), ("lama", "", 1))]
    COVERED = {"db.penjualan": {"metric": 2, "verified_query": 1, "draft": 1, "approved": 2, "rejected": 0},
               "db.cabang": {"metric": 1, "verified_query": 0, "draft": 0, "approved": 1, "rejected": 0},
               "db.lama": {"metric": 1, "verified_query": 0, "draft": 0, "approved": 0, "rejected": 1}}

    def page(self, **query):
        return drafts.table_page(self.TABLES, self.COVERED, {k: str(v) for k, v in query.items()})

    def test_status_counts_filters_sort_and_pages(self):
        result = self.page()
        self.assertEqual(result["counts"], {"none": 1, "draft": 0, "partial": 1, "approved": 1, "rejected": 1})
        self.assertEqual([r["status"] for r in result["tables"]], ["approved", "rejected", "partial", "none"])  # by name
        self.assertEqual([r["name"] for r in self.page(status="none")["tables"]], ["tiket"])
        self.assertEqual([r["name"] for r in self.page(q="KELUHAN")["tables"]], ["tiket"])
        self.assertEqual([r["name"] for r in self.page(sort="rows", dir="desc")["tables"]][0], "penjualan")
        second = self.page(size=10, page=1)
        self.assertEqual((second["total"], len(second["tables"])), (4, 4))
        self.assertEqual(self.page(size=10, page=2)["tables"], [])

    def test_bad_table_settings_are_refused(self):
        for query in ({"size": 7}, {"page": 0}, {"sort": "drop"}, {"dir": "up"}, {"status": "x"}, {"size": "1e3"}):
            with self.assertRaises(drafts.SettingsError):
                self.page(**query)


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
        request.body = mock.AsyncMock(return_value=json.dumps({"databases": ["kai_demo"], "updated_by": "a" * 24}).encode())
        with mock.patch.dict("os.environ", {"KAI_PLATFORM_INTERNAL_TOKEN": "secret"}), \
             mock.patch.object(drafts, "model_status", mock.AsyncMock(return_value="unavailable")), \
             mock.patch.object(drafts, "start") as start:
            response = await drafts.drafts_request(request)
        self.assertEqual(response.status_code, 409)
        self.assertIn("make llm-up", json.loads(response.body)["message"])
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
