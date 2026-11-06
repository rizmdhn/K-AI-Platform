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


async def fake_databases():
    return json.dumps(list(TABLES))


async def fake_tables(database, page_token=None, page_size=100, include_detailed_columns=True):
    return json.dumps({"tables": TABLES[database], "next_page_token": None})


def search(query, **kwargs):
    with mock.patch.object(metadata_search, "list_databases_async", fake_databases), \
         mock.patch.object(metadata_search, "list_tables_async", fake_tables):
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

    def test_rejects_bad_arguments(self):
        for query, limit in (("", 5), ("x" * 1001, 5), ("ok", 0), ("ok", 21)):
            with self.subTest(query=query[:5], limit=limit), self.assertRaises(GatewayError):
                search(query, limit=limit)


if __name__ == "__main__":
    unittest.main()
