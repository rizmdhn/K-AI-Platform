import asyncio
import json
import unittest
from unittest import mock

import per_user_middleware
import row_search
from tool_errors import GatewayError

ROWS = [[1, "Paket datang tepat waktu, kurir ramah"], [2, "Pesanan belum sampai sepuluh hari"],
        [3, "Saldo terpotong dua kali saat bayar"]]


def search(query, rows=ROWS, **kwargs):
    async def fake_query(sql):
        return json.dumps({"columns": ["ticket_id", "message"], "rows": rows})
    with mock.patch.object(row_search, "run_query_async", fake_query):
        return json.loads(asyncio.run(row_search.search_rows_by_meaning("SELECT 1", query, **kwargs)))


class RowSearchTests(unittest.TestCase):
    def test_keyword_fallback_ranks_rows_and_names_its_method(self):
        with mock.patch.object(row_search.metadata, "EMBEDDINGS_URL", ""):
            result = search("saldo terpotong", limit=2)
        self.assertEqual(result["columns"], ["ticket_id", "message", "score_keyword"])
        self.assertEqual(result["rows"][0][0], 3)
        self.assertEqual(len(result["rows"]), 2)

    def test_too_many_candidates_must_be_narrowed_not_silently_cut(self):
        rows = [[i, f"teks {i}"] for i in range(row_search.MAX_CANDIDATES + 1)]
        with self.assertRaises(GatewayError) as caught:
            search("teks", rows=rows)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_rows_without_text_are_refused(self):
        with self.assertRaises(GatewayError):
            search("apa saja", rows=[[1, 2], [3, 4]])

    def test_goes_through_the_gateway_sql_path(self):
        # SQL policy, readonly=1, the caller's login, output limits and audit all key off SQL_FIELDS.
        self.assertEqual(per_user_middleware.SQL_FIELDS["search_rows_by_meaning"], "sql")


if __name__ == "__main__":
    unittest.main()
