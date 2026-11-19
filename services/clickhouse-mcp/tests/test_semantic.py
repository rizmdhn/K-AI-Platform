import asyncio
import json
import unittest
from unittest import mock

import metadata_search
import semantic
from integration_settings import SettingsError

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
            page = {"definitions": [{"name": "x"}], "total": 1, "counts": {"draft": 1}, "storage_available": True}
            with mock.patch.object(semantic, "page_definitions", return_value=page) as listed:
                request = self.request("GET")
                request.query_params = {"status": "draft", "size": "50"}
                body = json.loads((await semantic.definitions_request(request)).body)
            self.assertEqual((body["definitions"], body["total"]), ([{"name": "x"}], 1))
            self.assertEqual(listed.call_args.args[0], {"status": "draft", "size": "50"})


class PagingTests(unittest.TestCase):
    def test_filters_build_parameterized_sql_and_escape_like(self):
        where, params = semantic._filters({"status": "draft", "kind": "metric", "database": "kai_demo",
                                            "table": "kai_demo.penjualan", "q": "50%_off"})
        self.assertIn("status = %s", where)
        self.assertIn("tables @> ARRAY[%s]::text[]", where)
        self.assertEqual(params[:4], ["draft", "metric", "kai_demo", "kai_demo.penjualan"])
        self.assertEqual(params[4], "%50\\%\\_off%")
        self.assertNotIn("50%_off", where)  # values never reach the SQL text

    def test_bad_filters_and_table_settings_are_refused_before_any_query(self):
        for query in ({"status": "live"}, {"kind": "x"}, {"database": "a;b"}, {"table": "nodot"}, {"q": "x" * 101}):
            with self.assertRaises(semantic.SettingsError):
                semantic._filters(query)
        for query in ({"size": "7"}, {"page": "0"}, {"sort": "definition"}, {"dir": "sideways"}, {"size": "abc"}):
            with self.assertRaises(semantic.SettingsError):
                semantic.page_definitions(query, env={})

    def test_bulk_needs_ids_or_a_narrowing_filter(self):
        env = {"KAI_CATALOG_DSN": "postgresql://x"}
        base = {"status": "approved", "updated_by": "b" * 24}
        for body in ({**base}, {**base, "ids": []}, {**base, "ids": ["not-a-uuid"]}, {**base, "ids": ["0" * 8 + "-0000-0000-0000-" + "0" * 12] * 1001},
                     {**base, "filter": {}}, {**base, "filter": {"status": "nope"}}, {**base, "status": "live", "ids": []}):
            with self.assertRaises(semantic.SettingsError):
                semantic.bulk_status(body, env)


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
