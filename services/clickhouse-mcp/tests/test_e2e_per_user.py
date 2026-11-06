"""End-to-end: mcp-clickhouse + per_user_middleware against a throwaway ClickHouse (P3-10).

Skipped unless MCP_E2E_URL is set. Setup (from services/clickhouse-mcp):
  docker run -d --name ch-e2e -p 127.0.0.1:18123:8123 -e CLICKHOUSE_PASSWORD=admin \
    -e CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 clickhouse/clickhouse-server:25.8
  docker exec -i ch-e2e clickhouse-client --password admin --multiquery < tests/e2e_clickhouse_init.sql
  PYTHONPATH=. CLICKHOUSE_HOST=127.0.0.1 CLICKHOUSE_PORT=18123 CLICKHOUSE_SECURE=false \
    CLICKHOUSE_USER=nogrant CLICKHOUSE_PASSWORD=nogrant-pw CLICKHOUSE_MCP_SERVER_TRANSPORT=http \
    CLICKHOUSE_MCP_AUTH_TOKEN=e2e-token MCP_MIDDLEWARE_MODULE=per_user_middleware KAI_BINDING_MODE=manual \
    python -m mcp_clickhouse.main &
  MCP_E2E_URL=http://127.0.0.1:8000/mcp python -m unittest tests.test_e2e_per_user
"""
import asyncio
import json
import os
import unittest
import urllib.request

URL = os.getenv("MCP_E2E_URL")
TOKEN = os.getenv("MCP_E2E_TOKEN", "e2e-token")
ALICE = {"X-CH-User": "alice", "X-CH-Password": "alice-pw"}
BOB = {"X-CH-User": "bob", "X-CH-Password": "bob-pw"}


def call(tool, args, headers, token=TOKEN):
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    async def run():
        auth = {"Authorization": f"Bearer {token}"} if token else {}
        async with Client(StreamableHttpTransport(URL, headers={**auth, **headers})) as client:
            result = await client.call_tool(tool, args, raise_on_error=False)
            text = "".join(getattr(c, "text", "") for c in result.content)
            return result.is_error, text

    return asyncio.run(run())


def admin(sql):
    """Change the throwaway fixture as its admin (CLICKHOUSE_PASSWORD=admin in the docker run above)."""
    import clickhouse_connect

    clickhouse_connect.get_client(host="127.0.0.1", port=18123, username="default",
                                  password=os.getenv("MCP_E2E_CH_ADMIN_PASSWORD", "admin")).command(sql)


def rows(text):
    return json.loads(text)["rows"]


@unittest.skipUnless(URL, "set MCP_E2E_URL to run against a live mcp-clickhouse")
class PerUserEndToEnd(unittest.TestCase):
    SQL = "SELECT region, sum(amount) FROM sales.orders GROUP BY region ORDER BY region"

    def test_health_endpoints_are_available_without_tool_credentials(self):
        origin = URL.rsplit("/mcp", 1)[0]
        for path in ("/health/live", "/health/ready"):
            with self.subTest(path=path), urllib.request.urlopen(origin + path, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"OK")

    def test_identity_is_the_callers_own_user(self):
        for headers, expected in ((ALICE, "alice"), (BOB, "bob")):
            failed, text = call("run_query", {"query": "SELECT currentUser()"}, headers)
            self.assertFalse(failed, text)
            self.assertEqual(rows(text), [[expected]])

    def test_server_side_limits_reach_clickhouse(self):
        sql = "SELECT getSetting('readonly'), getSetting('max_execution_time'), getSetting('max_rows_to_read')"
        failed, text = call("run_query", {"query": sql}, ALICE)
        self.assertFalse(failed, text)
        self.assertEqual([float(v) for v in rows(text)[0]], [1, 25, 10**9])

    def test_dba_const_profile_wins_over_gateway_limits(self):
        carol = {"X-CH-User": "carol", "X-CH-Password": "carol-pw"}
        sql = "SELECT getSetting('readonly'), getSetting('max_execution_time'), getSetting('max_memory_usage'), count() FROM sales.orders"
        failed, text = call("run_query", {"query": sql}, carol)
        self.assertFalse(failed, text)
        self.assertEqual([float(v) for v in rows(text)[0]], [2, 300, 10**10, 3])

    def test_account_pinned_writable_is_refused(self):
        failed, text = call("run_query", {"query": "SELECT count() FROM sales.orders"}, {"X-CH-User": "dave", "X-CH-Password": "dave-pw"})
        self.assertTrue(failed)
        self.assertIn("ACCESS_DENIED", text)

    def test_same_query_respects_each_users_row_policy(self):
        self.assertEqual(rows(call("run_query", {"query": self.SQL}, ALICE)[1]), [["ID", 300]])
        self.assertEqual(rows(call("run_query", {"query": self.SQL}, BOB)[1]), [["SG", 50]])

    def test_table_grants_are_enforced_by_clickhouse(self):
        failed, _ = call("run_query", {"query": "SELECT * FROM sales.secret"}, ALICE)
        self.assertFalse(failed)
        failed, text = call("run_query", {"query": "SELECT * FROM sales.secret"}, BOB)
        self.assertTrue(failed)
        self.assertNotIn("alice only", text)

    def test_metadata_shows_only_granted_tables(self):
        failed, text = call("list_tables", {"database": "sales"}, BOB)
        self.assertFalse(failed, text)
        names = {t["name"] for t in json.loads(text)["tables"]}
        self.assertEqual(names, {"orders"})

    def test_table_schema_respects_user_grants(self):
        for headers in (ALICE, BOB):
            failed, text = call("get_table_schema", {"database": "sales", "table": "orders"}, headers)
            self.assertFalse(failed, text)
            columns = json.loads(text)["columns"]
            self.assertEqual([(c["name"], c["type"]) for c in columns],
                             [("region", "String"), ("amount", "UInt32")])
        failed, _ = call("get_table_schema", {"database": "sales", "table": "secret"}, ALICE)
        self.assertFalse(failed)
        errors = []
        for table in ("secret", "missing_table"):
            failed, text = call("get_table_schema", {"database": "sales", "table": table}, BOB)
            self.assertTrue(failed)
            payload = json.loads(text)
            self.assertTrue(payload["request_id"])
            errors.append((payload["code"], payload["message"]))
        self.assertEqual(errors[0], errors[1])

    def test_schema_lookup_treats_like_characters_as_literal(self):
        admin("CREATE TABLE sales.`orders_%` (literal_column String) ENGINE=Memory")
        admin("GRANT SELECT ON sales.`orders_%` TO alice")
        try:
            failed, text = call("get_table_schema", {"database": "sales", "table": "orders_%"}, ALICE)
            self.assertFalse(failed, text)
            self.assertEqual([c["name"] for c in json.loads(text)["columns"]], ["literal_column"])
        finally:
            admin("DROP TABLE sales.`orders_%`")

    def test_missing_or_wrong_credentials_are_denied(self):
        for headers in ({}, {"X-CH-User": "alice"}, {"X-CH-User": "{{CH_USER}}", "X-CH-Password": "x"},
                        {"X-CH-User": "alice", "X-CH-Password": "wrong"}):
            failed, text = call("run_query", {"query": "SELECT currentUser()"}, headers)
            self.assertTrue(failed, headers)
            self.assertNotIn("nogrant", text)
            payload = json.loads(text)
            self.assertEqual(payload["code"], "AUTH_REQUIRED")
            self.assertEqual(len(payload["request_id"]), 32)

    def test_mcp_bearer_token_is_required(self):
        with self.assertRaises(Exception):
            call("run_query", {"query": "SELECT 1"}, ALICE, token="wrong-token")

    def test_writes_and_unsafe_sql_are_blocked(self):
        for sql in ("INSERT INTO sales.orders VALUES ('SG', 1)", "SELECT 1; DROP TABLE sales.orders",
                    "SELECT * FROM system.users", "SELECT 1 WHERE 1 IN system.users",
                    "SELECT * FROM url('http://example.com/x', CSV, 'a String')"):
            failed, _ = call("run_query", {"query": sql}, ALICE)
            self.assertTrue(failed, sql)
        self.assertEqual(rows(call("run_query", {"query": "SELECT count() FROM sales.orders"}, ALICE)[1]), [[2]])

    def test_revoked_grant_applies_on_next_query(self):
        sql = {"query": "SELECT count() FROM sales.orders"}
        schema = {"database": "sales", "table": "orders"}
        self.assertFalse(call("run_query", sql, BOB)[0])
        self.assertFalse(call("get_table_schema", schema, BOB)[0])
        admin("REVOKE SELECT ON sales.orders FROM bob")
        try:
            failed, text = call("run_query", sql, BOB)
            self.assertTrue(failed)
            self.assertIn("ACCESS_DENIED", text)
            failed, text = call("get_table_schema", schema, BOB)
            self.assertTrue(failed)
            self.assertIn("Table not found or not accessible", text)
        finally:
            admin("GRANT SELECT ON sales.orders TO bob")

    def test_password_change_applies_on_next_query(self):
        sql = {"query": "SELECT currentUser()"}
        self.assertFalse(call("run_query", sql, ALICE)[0])  # warms the cached client
        admin("ALTER USER alice IDENTIFIED BY 'alice-new-pw'")
        try:
            self.assertTrue(call("run_query", sql, ALICE)[0])
            renewed = {**ALICE, "X-CH-Password": "alice-new-pw"}
            self.assertEqual(rows(call("run_query", sql, renewed)[1]), [["alice"]])
        finally:
            admin("ALTER USER alice IDENTIFIED BY 'alice-pw'")

    def test_clickhouse_session_is_readonly(self):
        failed, text = call("run_query", {"query": "SELECT getSetting('readonly')"}, ALICE)
        self.assertFalse(failed, text)
        self.assertEqual(str(rows(text)[0][0]), "1")

    def test_execute_readonly_sql_uses_personal_identity_and_row_policies(self):
        for headers, user, expected in ((ALICE, "alice", [["ID", 300]]), (BOB, "bob", [["SG", 50]])):
            failed, text = call("execute_readonly_sql", {"sql": self.SQL, "purpose": "Compare regional totals"}, headers)
            self.assertFalse(failed, text)
            payload = json.loads(text)
            self.assertEqual(payload["rows"], expected)
            self.assertEqual(len(payload["request_id"]), 32)
            failed, text = call("execute_readonly_sql", {"sql": "SELECT currentUser(), getSetting('readonly')", "purpose": "Check caller"}, headers)
            self.assertFalse(failed, text)
            row = rows(text)[0]
            self.assertEqual(row[0], user)
            self.assertEqual(str(row[1]), "1")

    def test_execute_readonly_sql_rejects_identity_and_writes(self):
        valid = {"sql": "SELECT currentUser()", "purpose": "Check caller"}
        for field in ("credential_ref", "clickhouse_user", "role", "session_user", "password"):
            failed, text = call("execute_readonly_sql", {**valid, field: "spoofed"}, ALICE)
            self.assertTrue(failed)
            self.assertEqual(json.loads(text)["code"], "INVALID_ARGUMENT")
        failed, text = call("execute_readonly_sql", {"sql": "DROP TABLE sales.orders", "purpose": "Attempt write"}, ALICE)
        self.assertTrue(failed)
        self.assertEqual(json.loads(text)["code"], "UNSAFE_SQL")

    def test_execute_readonly_sql_caps_rows_and_bytes(self):
        admin("CREATE TABLE sales.large_result (n UInt64, value String) ENGINE=Memory")
        try:
            admin("INSERT INTO sales.large_result SELECT number, repeat('雪',2000) FROM numbers(1001)")
            admin("GRANT SELECT ON sales.large_result TO alice")
            failed, text = call("execute_readonly_sql", {"sql": "SELECT n FROM sales.large_result ORDER BY n", "purpose": "Check row limit"}, ALICE)
            self.assertFalse(failed, text)
            payload = json.loads(text)
            self.assertEqual(payload["row_count"], 1000)
            self.assertTrue(payload["truncated"])
            failed, text = call("execute_readonly_sql", {"sql": "SELECT value FROM sales.large_result ORDER BY n", "purpose": "Check byte limit"}, ALICE)
            self.assertFalse(failed, text)
            self.assertLessEqual(len(text.encode("utf-8")), 1_048_576)
            self.assertTrue(json.loads(text)["truncated"])
        finally:
            admin("DROP TABLE sales.large_result")


if __name__ == "__main__":
    unittest.main()
