"""OpenMetadata client against a fake HTTP server on 127.0.0.1: paging, auth header, suggestions, check output."""
import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import openmetadata

TOKEN = "bot-token-never-printed"
TABLES = [
    {"name": "orders", "fullyQualifiedName": "ch.default.gold.orders", "description": "Pesanan",
     "databaseSchema": {"name": "gold"}, "tags": [{"tagFQN": "Glossary.Omzet"}],
     "columns": [{"name": "amount", "description": "Nilai"}, {"name": "region", "description": ""}]},
    {"name": "raw", "fullyQualifiedName": "ch.default.bronze.raw", "description": "",
     "databaseSchema": {"name": "bronze"}, "columns": [{"name": "x"}]},
]


class Handler(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *args):
        pass

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        Handler.seen.append(("GET", self.path, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"message": "no"})
        if self.path == "/api/v1/system/version":
            return self._send(200, {"version": "1.5.6"})
        if self.path.startswith("/api/v1/tables?"):
            first = "after=" not in self.path
            return self._send(200, {"data": TABLES[:1] if first else TABLES[1:], "paging": {"after": "p2"} if first else {}})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.seen.append(("POST", self.path, body))
        self._send(201, {})


class OpenMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.env = {"KAI_OPENMETADATA_URL": f"http://127.0.0.1:{cls.server.server_port}/",
                   "KAI_OPENMETADATA_TOKEN": TOKEN, "KAI_OPENMETADATA_SERVICE": "ch"}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_lists_all_pages_and_builds_the_search_view(self):
        client, service = openmetadata.from_env(self.env)
        tables = list(client.tables(service))
        self.assertEqual([openmetadata.key(t) for t in tables], [("gold", "orders"), ("bronze", "raw")])
        self.assertEqual(openmetadata.view(tables[0]), {"description": "Pesanan", "columns": {"amount": "Nilai"}, "tags": ["Glossary.Omzet"]})
        self.assertIsNone(openmetadata.view(tables[1]))

    def test_suggestion_payload_and_entity_links(self):
        client, _ = openmetadata.from_env(self.env)
        client.suggest(openmetadata.entity_link("ch.default.gold.orders", "a.b"), "Teks")
        method, path, body = Handler.seen[-1]
        self.assertEqual((method, path), ("POST", "/api/v1/suggestions"))
        self.assertEqual(body, {"type": "SuggestDescription", "entityLink": '<#E::table::ch.default.gold.orders::columns::"a.b">', "description": "Teks"})

    def test_check_reports_version_and_counts_without_the_token(self):
        lines = []
        self.assertEqual(openmetadata.check({**self.env, "KAI_CATALOG_DATABASES": "gold,silver"}, lines.append), 0)
        text = "\n".join(lines)
        self.assertIn("OpenMetadata 1.5.6", text)
        self.assertIn("Suggestions API: available", text)
        self.assertIn("2 table(s) in 2 database(s); 1 table(s) and 2 of 3 column(s) without a description", text)
        self.assertIn("silver", text)
        self.assertIn("WARNING: http://", text)
        self.assertNotIn(TOKEN, text)

    def test_wrong_token_and_bad_config_fail_with_fixed_messages(self):
        lines = []
        self.assertEqual(openmetadata.check({**self.env, "KAI_OPENMETADATA_TOKEN": "wrong"}, lines.append), 1)
        self.assertIn("FAILED: HTTP 401", lines[-1])
        self.assertNotIn("wrong", lines[-1])
        for env in ({**self.env, "KAI_OPENMETADATA_URL": "ftp://x"}, {**self.env, "KAI_OPENMETADATA_SERVICE": ""}):
            with self.assertRaises(ValueError):
                openmetadata.from_env(env)
        self.assertEqual(openmetadata.check({}, lines.append), 1)

    def test_versions_and_cache_degrade_safely(self):
        self.assertTrue(openmetadata.supports_suggestions("1.3.0"))
        self.assertFalse(openmetadata.supports_suggestions("1.2.9"))
        self.assertFalse(openmetadata.supports_suggestions(""))
        with mock.patch.dict(openmetadata.os.environ, self.env, clear=True), \
             mock.patch.object(openmetadata, "_cache", {"at": -10**9, "data": {}}):
            self.assertIn(("gold", "orders"), asyncio.run(openmetadata.approved_metadata()))
        with mock.patch.dict(openmetadata.os.environ, {}, clear=True):
            self.assertEqual(asyncio.run(openmetadata.approved_metadata()), {})

    def test_database_summary_counts_description_gaps(self):
        tables = [{"databaseSchema": {"name": "gold"}, "description": "ok", "columns": [{"name": "a"}, {"name": "b", "description": "x"}]},
                  {"databaseSchema": {"name": "gold"}, "columns": [{"name": "c"}]},
                  {"databaseSchema": {"name": "bronze"}, "description": " ", "columns": []}, {"columns": []}]
        self.assertEqual(openmetadata.database_summary(tables), [
            {"name": "bronze", "tables": 1, "empty_tables": 1, "empty_columns": 0},
            {"name": "gold", "tables": 2, "empty_tables": 1, "empty_columns": 2}])


if __name__ == "__main__":
    unittest.main()
