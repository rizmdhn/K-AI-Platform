import asyncio
import json
import unittest
from unittest import mock

import metadata_search
from tool_errors import GatewayError

TABLES = {
    "gold": [
        {"database": "gold", "name": "penjualan", "comment": "Penjualan per region",
         "columns": [{"name": "region", "column_type": "String"}, {"name": "amount", "column_type": "UInt32"}]},
        {"database": "gold", "name": "cuaca", "comment": "Data cuaca harian",
         "columns": [{"name": "suhu", "column_type": "Float32"}]},
    ],
    "system": [{"database": "system", "name": "query_log", "comment": "", "columns": []}],
}


async def fake_run_query(sql):
    visible = [t for db, ts in TABLES.items() if db != "system" for t in ts]  # the SQL filters system databases
    if "system.tables" in sql:
        return json.dumps({"columns": ["database", "name", "comment", "total_rows"],
                           "rows": [[t["database"], t["name"], t["comment"], t.get("total_rows", 10)] for t in visible]})
    return json.dumps({"columns": ["database", "table", "name", "type", "comment"],
                       "rows": [[t["database"], t["name"], c["name"], c["column_type"], ""] for t in visible for c in t["columns"]]})


def search(query, **kwargs):
    with mock.patch.object(metadata_search, "run_query_async", fake_run_query):
        return json.loads(asyncio.run(metadata_search.search_database_metadata(query, **kwargs)))


class MetadataSearchTests(unittest.TestCase):
    def test_keyword_fallback_ranks_and_never_lists_system_tables(self):
        with mock.patch.object(metadata_search, "EMBEDDINGS_URL", ""):
            result = search("penjualan per region", limit=5)
        self.assertEqual(result["method"], "keyword")
        self.assertEqual(result["results"][0]["table"], "penjualan")
        self.assertNotIn("system", {r["database"] for r in result["results"]})

    def test_embedding_outage_falls_back_instead_of_failing(self):
        with mock.patch.object(metadata_search, "EMBEDDINGS_URL", "http://127.0.0.1:9/v1/embeddings"):
            self.assertEqual(search("cuaca", limit=1)["method"], "keyword")

    def test_very_wide_table_description_is_capped(self):
        wide = {"database": "gold", "name": "lebar", "comment": "", "columns": [{"name": f"kolom_{i}", "column_type": "String"} for i in range(2000)]}
        self.assertEqual(len(metadata_search._describe(wide)), metadata_search.MAX_DESCRIPTION_CHARS)

    def test_openmetadata_enriches_only_visible_tables_and_columns(self):
        catalog = {("gold", "cuaca"): {"description": "Omzet toko harian.", "tags": ["Glossary.Omzet"],
                                       "columns": {"suhu": "Nilai", "rahasia": "kolom yang tidak di-grant"}},
                   ("hr", "gaji"): {"description": "Gaji karyawan."}}
        async def fake_catalog():
            return catalog
        with mock.patch.object(metadata_search, "EMBEDDINGS_URL", ""), mock.patch.object(metadata_search, "approved_metadata", fake_catalog):
            result = search("omzet", limit=5)
        top = result["results"][0]
        self.assertEqual((top["table"], top["description"], top["column_meanings"]), ("cuaca", "Omzet toko harian.", {"suhu": "Nilai"}))
        self.assertIn("OpenMetadata", top["description_source"])
        text = json.dumps(result)
        self.assertNotIn("gaji", text.lower())
        self.assertNotIn("rahasia", text)
        self.assertNotIn("description", result["results"][1])

    def test_scratch_copies_and_bronze_rank_below_gold(self):
        extra = [{"database": "gold", "name": "penjualan_coba", "comment": "Penjualan per region",
                  "columns": [{"name": "region", "column_type": "String"}, {"name": "amount", "column_type": "UInt32"}]},
                 {"database": "bronze", "name": "penjualan", "comment": "Penjualan per region",
                  "columns": [{"name": "region", "column_type": "String"}, {"name": "amount", "column_type": "UInt32"}]}]
        with mock.patch.dict(TABLES, {"gold": TABLES["gold"] + extra[:1], "bronze": extra[1:]}), \
             mock.patch.object(metadata_search, "EMBEDDINGS_URL", ""):
            result = search("penjualan per region", limit=3)
        self.assertEqual([(r["database"], r["table"]) for r in result["results"]][0], ("gold", "penjualan"))

    def test_catalog_is_cached_per_user(self):
        calls = []
        async def counting(sql):
            calls.append(sql)
            return await fake_run_query(sql)
        metadata_search._catalogs.clear()
        with mock.patch.object(metadata_search, "run_query_async", counting), \
             mock.patch.object(metadata_search.clients, "_get_client_config_overrides", lambda: {"username": "alice"}):
            asyncio.run(metadata_search._visible_tables())
            asyncio.run(metadata_search._visible_tables())
        self.assertEqual(len(calls), 2)  # tables + columns once, second call served from cache
        metadata_search._catalogs.clear()

    def test_rejects_bad_arguments(self):
        for query, limit in (("", 5), ("x" * 1001, 5), ("ok", 0), ("ok", 21)):
            with self.subTest(query=query[:5], limit=limit), self.assertRaises(GatewayError):
                search(query, limit=limit)


if __name__ == "__main__":
    unittest.main()
