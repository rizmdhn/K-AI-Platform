import asyncio
import io
import json
import os
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

import batch_jobs
import documents
from tool_errors import GatewayError

OWNER, OTHER = "a" * 24, "b" * 24
MD = """# Ringkasan

Pendapatan **naik** 12%.

- Jakarta
- Bandung

1. Langkah satu

| Kota | Penumpang |
|---|---:|
| Jakarta | 1200 |
| =cmd | 5 |
"""


def as_user(owner):
    return mock.patch.object(batch_jobs, "get_http_headers", lambda: {"x-librechat-user-id": owner})


class Request:
    def __init__(self, link, bearer="Bearer svc"):
        url = urlparse(link)
        file_id, ext = url.path.rsplit("/", 1)[1].split(".")
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.headers = {"authorization": bearer}
        self.path_params = {"file_id": file_id, "ext": ext}
        self.query_params = query


def make(owner=OWNER, **kwargs):
    with as_user(owner):
        return json.loads(asyncio.run(documents.create_document(**kwargs)))


class DocumentTests(unittest.TestCase):
    def setUp(self):
        documents._files.clear()
        self.env = mock.patch.dict(os.environ, {"CLICKHOUSE_MCP_AUTH_TOKEN": "svc"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def download(self, link, **kw):
        return asyncio.run(documents.download_request(Request(link, **kw)))

    def test_markdown_blocks(self):
        kinds = [k for k, _ in documents.blocks(MD)]
        self.assertEqual(kinds, ["h1", "p", "li", "li", "ol", "table"])
        self.assertEqual(documents.blocks(MD)[-1][1][2], ["=cmd", "5"])

    def test_docx_pdf_xlsx_csv_from_markdown(self):
        from docx import Document
        from openpyxl import load_workbook

        docx = make(format="docx", title="Laporan Q3", content=MD)
        body = self.download(docx["link"]).body
        text = "\n".join(p.text for p in Document(io.BytesIO(body)).paragraphs)
        self.assertIn("Pendapatan naik 12%.", text)
        self.assertEqual(docx["file_name"], "Laporan-Q3.docx")
        self.assertTrue(self.download(make(format="pdf", title="Laporan", content=MD)["link"]).body.startswith(b"%PDF"))
        book = load_workbook(io.BytesIO(self.download(make(format="xlsx", title="Data", content=MD)["link"]).body))
        rows = list(book.active.values)
        self.assertEqual(rows[0], ("Kota", "Penumpang"))
        self.assertEqual(rows[1], ("Jakarta", 1200))
        self.assertEqual(rows[2][0], "'=cmd")  # formula injection guard
        csv = self.download(make(format="csv", title="Data", content=MD)["link"]).body.decode()
        self.assertIn("'=cmd,5", csv)

    def test_xlsx_from_sql_and_row_limit(self):
        async def rows(sql):
            self.assertIn(f"LIMIT {documents.MAX_ROWS + 1}", sql)
            return json.dumps({"columns": ["kota", "n"], "rows": [["Jakarta", 1]]})

        with mock.patch.object(documents, "run_query_async", rows):
            self.assertIn("link", make(format="xlsx", title="Data", sql="SELECT kota, n FROM t"))

        async def too_many(sql):
            return json.dumps({"columns": ["n"], "rows": [[i] for i in range(documents.MAX_ROWS + 1)]})

        with mock.patch.object(documents, "run_query_async", too_many), self.assertRaises(GatewayError) as error:
            make(format="csv", title="Data", sql="SELECT n FROM t")
        self.assertEqual(error.exception.code, "QUERY_LIMIT")
        self.assertIn("50,000", str(error.exception))

    def test_clear_errors(self):
        for kwargs in ({"format": "docx", "title": "x", "content": ""}, {"format": "pdf", "title": "x", "content": "a", "sql": "SELECT 1"},
                       {"format": "xlsx", "title": "x", "content": "no table"}):
            with self.assertRaises(GatewayError) as error:
                make(**kwargs)
            self.assertEqual(error.exception.code, "INVALID_ARGUMENT")
        with mock.patch.object(documents, "MAX_FILE_BYTES", 10), self.assertRaises(GatewayError) as error:
            make(format="docx", title="x", content=MD)
        self.assertEqual(error.exception.code, "QUERY_LIMIT")
        with self.assertRaises(GatewayError) as error:
            make(format="xlsx", title="x", sql="DROP TABLE t")
        self.assertEqual(error.exception.code, "UNSAFE_SQL")

    def test_link_signed_for_owner_and_expires(self):
        link = make(format="docx", title="x", content=MD)["link"]
        self.assertEqual(self.download(link, bearer="Bearer other").status_code, 401)
        self.assertEqual(self.download(link.replace("sig=", "sig=0")).status_code, 403)
        self.assertEqual(self.download(link.replace(".docx", ".pdf")).status_code, 403)
        with mock.patch.object(documents.time, "time", lambda: 9_999_999_999):
            self.assertEqual(self.download(link).status_code, 403)

    def test_store_cap_and_daily_limit(self):
        with mock.patch.object(documents, "MAX_STORE_BYTES", 1):
            make(format="csv", title="a", content=MD)
            make(format="csv", title="b", content=MD)
        self.assertEqual(len(documents._files), 1)
        with mock.patch.object(documents, "FILES_PER_DAY", 1), self.assertRaises(GatewayError):
            make(format="csv", title="c", content=MD)
        make(OTHER, format="csv", title="d", content=MD)


if __name__ == "__main__":
    unittest.main()
