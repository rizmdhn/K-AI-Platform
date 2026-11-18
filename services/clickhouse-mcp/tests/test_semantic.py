import asyncio
import json
import unittest
from unittest import mock

import metadata_search
import semantic
from integration_settings import SettingsError
from tool_errors import GatewayError

METRIC = {"kind": "metric", "name": "pendapatan_bersih", "table": "kai_demo.penjualan", "expression": "sum(pendapatan_rp)",
          "metric_type": "sum", "unit": "Rupiah", "time_column": "tanggal", "dimensions": "cabang, kategori",
          "synonyms": ["omzet", "revenue"], "description": "Pendapatan setelah diskon."}
QUERY = {"kind": "verified_query", "name": "pertumbuhan_cabang", "question": "Pertumbuhan pendapatan per cabang",
         "sql": "WITH m AS (SELECT cabang, toStartOfMonth(tanggal) b, sum(pendapatan_rp) p FROM kai_demo.penjualan "
                "GROUP BY cabang, b) SELECT * FROM m JOIN kai_demo.pelanggan USING (cabang)"}


def approved(kind, name, tables, **definition):
    return {"definition_id": "x", "kind": kind, "name": name, "tables": tables, "status": "approved",
            "definition": {"description": "", "synonyms": [], **definition}}


class CleanTests(unittest.TestCase):
    def test_metric_is_normalized_and_bound_to_its_table(self):
        kind, name, tables, definition = semantic.clean(METRIC)
        self.assertEqual((kind, name, tables), ("metric", "pendapatan_bersih", ["kai_demo.penjualan"]))
        self.assertEqual(definition["dimensions"], ["cabang", "kategori"])
        self.assertEqual(semantic.metric_sql(name, definition, tables[0]),
                         "SELECT sum(pendapatan_rp) AS pendapatan_bersih FROM kai_demo.penjualan")

    def test_verified_query_lists_every_real_table_but_not_ctes(self):
        self.assertEqual(semantic.clean(QUERY)[2], ["kai_demo.pelanggan", "kai_demo.penjualan"])

    def test_unsafe_or_ambiguous_definitions_are_refused(self):
        for change in [
            {"name": "Pendapatan Bersih"},
            {"kind": "dashboard"},
            {"table": "penjualan"},
            {"expression": ""},
            {"expression": "sum(x) FROM kai_demo.pelanggan --"},  # would read another table than declared
            {"expression": "1; DROP TABLE kai_demo.penjualan"},
            {"filter": "cabang IN (SELECT cabang FROM rahasia.gaji)"},
            {"time_column": "tanggal; DROP"},
            {"dimensions": ["ok", "bad name"]},
            {"metric_type": "magic"},
        ]:
            with self.subTest(change=change), self.assertRaises(SettingsError):
                semantic.clean({**METRIC, **change})
        for change in [{"sql": "SELECT * FROM penjualan"}, {"sql": "DELETE FROM kai_demo.penjualan WHERE 1"},
                       {"sql": "SELECT * FROM system.users"}, {"question": ""}]:
            with self.subTest(change=change), self.assertRaises(SettingsError):
                semantic.clean({**QUERY, **change})


class ProposeTests(unittest.IsolatedAsyncioTestCase):
    def patches(self, columns=("tanggal", "cabang", "kategori", "pendapatan_rp"), explain=None):
        schema = json.dumps({"columns": [{"name": c} for c in columns]})
        saved = []
        return saved, [
            mock.patch.object(semantic, "get_table_schema", mock.AsyncMock(return_value=schema)),
            mock.patch.object(semantic, "run_query_async", mock.AsyncMock(side_effect=explain)),
            mock.patch.object(semantic, "save_draft", lambda cleaned, user: saved.append((cleaned, user))),
            mock.patch.object(semantic, "get_http_headers", lambda: {"x-librechat-user-id": "a" * 24}),
        ]

    async def call(self, patches, **args):
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return await semantic.propose_semantic_definition(**args)

    async def test_valid_metric_is_explained_and_saved_as_draft_for_the_proposer(self):
        saved, patches = self.patches()
        result = json.loads(await self.call(patches, **{**METRIC, "dimensions": ["cabang", "kategori"]}))
        self.assertEqual(result["saved"], "draft")
        self.assertEqual(saved[0][1], "a" * 24)
        semantic.run_query_async.assert_awaited_once_with(
            "EXPLAIN SELECT sum(pendapatan_rp) AS pendapatan_bersih FROM kai_demo.penjualan")

    async def test_unknown_column_or_clickhouse_rejection_is_reported_not_saved(self):
        saved, patches = self.patches(columns=("tanggal",))
        with self.assertRaises(GatewayError) as caught:
            await self.call(patches, **{**METRIC, "dimensions": ["cabang"]})
        self.assertIn("cabang", str(caught.exception))
        saved, patches = self.patches(explain=RuntimeError("Unknown identifier"))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with self.assertRaises(GatewayError):
            await semantic.propose_semantic_definition(**{**METRIC, "dimensions": []})
        self.assertEqual(saved, [])


class SearchTests(unittest.TestCase):
    def test_approved_definitions_only_for_callers_who_see_every_table(self):
        from test_metadata_search import fake_run_query
        rows = [approved("metric", "omzet_region", ["gold.penjualan"], expression="sum(amount)", synonyms=["omzet"]),
                approved("verified_query", "omzet_rahasia", ["gold.penjualan", "hr.gaji"],
                         question="omzet dan gaji", sql="SELECT 1 FROM gold.penjualan, hr.gaji")]

        async def rows_now():
            return rows
        with mock.patch.object(metadata_search, "EMBEDDINGS_URL", ""), \
             mock.patch.object(metadata_search, "run_query_async", fake_run_query), \
             mock.patch.object(semantic, "approved", rows_now):
            result = json.loads(asyncio.run(metadata_search.search_database_metadata("berapa omzet per region")))
        self.assertEqual([d["name"] for d in result["semantic"]], ["omzet_region"])
        self.assertEqual(result["semantic"][0]["sql"], "SELECT sum(amount) AS omzet_region FROM gold.penjualan")


class RouteTests(unittest.IsolatedAsyncioTestCase):
    def request(self, method, body=None, definition_id=None, token="secret"):
        request = mock.Mock(method=method, path_params={"definition_id": definition_id} if definition_id else {},
                            headers={"authorization": f"Bearer {token}"})
        request.body = mock.AsyncMock(return_value=json.dumps(body or {}).encode())
        return request

    async def test_routes_need_the_internal_token_and_validate_before_writing(self):
        with mock.patch.dict("os.environ", {"KAI_PLATFORM_INTERNAL_TOKEN": "secret", "KAI_CATALOG_DSN": "postgresql://x"}):
            self.assertEqual((await semantic.definitions_request(self.request("GET", token="wrong"))).status_code, 401)
            with mock.patch.object(semantic, "create") as create:
                response = await semantic.definitions_request(
                    self.request("POST", {**METRIC, "expression": "", "updated_by": "b" * 24}))
                self.assertEqual(response.status_code, 400)
                create.assert_not_called()
                response = await semantic.definitions_request(self.request("PUT", {**METRIC, "updated_by": "b" * 24}, "not-a-uuid"))
                self.assertEqual(response.status_code, 400)
            with mock.patch.object(semantic, "list_definitions", return_value=[{"name": "x"}]):
                body = json.loads((await semantic.definitions_request(self.request("GET"))).body)
            self.assertEqual((body["definitions"], body["storage_available"]), ([{"name": "x"}], True))


class FakeConnection:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        result = mock.Mock()
        result.fetchone.return_value = (self.status,) if self.status else None
        return result


class DraftTests(unittest.TestCase):
    def test_agent_cannot_overwrite_a_reviewed_definition(self):
        for status in ("approved", "rejected"):
            with self.subTest(status=status), mock.patch.object(semantic, "_connect", lambda env: FakeConnection(status)):
                with self.assertRaises(SettingsError):
                    semantic.save_draft(semantic.clean(METRIC), None, {"KAI_CATALOG_DSN": "postgresql://x"})


if __name__ == "__main__":
    unittest.main()
