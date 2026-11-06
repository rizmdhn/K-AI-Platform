import hashlib
import json
import unittest
from unittest import mock

from fastmcp import Client, FastMCP

import per_user_middleware as mw
import sql_tool


@mock.patch.dict('os.environ', {'KAI_BINDING_MODE': 'manual'})
class ExecuteSQLTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_enabled_configuration_refuses_startup(self):
        for flag in ("CLICKHOUSE_ALLOW_WRITE_ACCESS", "CLICKHOUSE_ALLOW_DROP"):
            with self.subTest(flag=flag), mock.patch.dict("os.environ", {flag: "true"}):
                with self.assertRaisesRegex(RuntimeError, "read-only"):
                    mw.setup_middleware(FastMCP("unsafe-configuration"))

    async def test_reuses_personal_connection_and_returns_bounded_traceable_rows(self):
        server = FastMCP("readonly-integration")
        mw.setup_middleware(server)
        observed = []

        async def query(sql):
            observed.append((sql, await mw.get_context().get_state(mw.CLIENT_CONFIG_OVERRIDES_KEY)))
            return json.dumps({"columns": ["value"], "rows": [[i] for i in range(1001)]})

        with mock.patch.object(sql_tool, "run_query_async", side_effect=query), \
             mock.patch.object(mw.audit, "info") as log:
            async with Client(server) as client:
                tools = await client.list_tools()
                alias = next(t for t in tools if t.name == "execute_readonly_sql")
                self.assertEqual(set(alias.input_schema["required"]), {"sql", "purpose"})
                for user in ("alice", "bob"):
                    with mock.patch.object(mw, "get_http_headers", return_value={"x-ch-user": user, "x-ch-password": "fixture-password"}):
                        result = await client.call_tool("execute_readonly_sql", {"sql": "SELECT 1", "purpose": "private-purpose"})
                    text = result.content[0].text
                    payload = json.loads(text)
                    audit = json.loads(log.call_args.args[0])
                    self.assertEqual(len(payload["rows"]), 1000)
                    self.assertTrue(payload["truncated"])
                    self.assertEqual(payload["row_count"], 1000)
                    self.assertEqual(payload["request_id"], audit["request_id"])
                    self.assertEqual(audit["purpose_sha256"], hashlib.sha256(b"private-purpose").hexdigest())
                    self.assertNotIn("private-purpose", json.dumps(audit))
                    self.assertNotIn("fixture-password", json.dumps(audit))
                    self.assertEqual(result.structured_content, {"result": text})
                    self.assertLessEqual(len(text.encode()), 1_048_576)
        self.assertEqual([state["username"] for _, state in observed], ["alice", "bob"])
        self.assertEqual([state["settings"]["readonly"] for _, state in observed], [1, 1])
        self.assertEqual([sql for sql, _ in observed], ["SELECT 1", "SELECT 1"])

    async def test_invalid_or_identity_arguments_never_reach_database(self):
        server = FastMCP("argument-integration")
        mw.setup_middleware(server)
        valid = {"sql": "SELECT 1", "purpose": "Inspect data"}
        cases = [{"sql": "SELECT 1"}, {**valid, "purpose": "  "}, {**valid, "purpose": "x" * 501}]
        for field in ("credential_ref", "clickhouse_user", "role", "session_user", "password", "params"):
            cases.append({**valid, field: "spoofed"})
        with mock.patch.object(sql_tool, "run_query_async", side_effect=AssertionError("backend must not run")), \
             mock.patch.object(mw, "get_http_headers", return_value={"x-ch-user": "alice", "x-ch-password": "fixture"}), \
             mock.patch.object(mw.audit, "info"):
            async with Client(server) as client:
                for args in cases:
                    with self.subTest(args=args):
                        result = await client.call_tool("execute_readonly_sql", args, raise_on_error=False)
                        self.assertTrue(result.is_error)
                        payload = json.loads(result.content[0].text)
                        self.assertEqual(payload["code"], "INVALID_ARGUMENT")

    async def test_auth_and_sql_policy_apply_to_alias(self):
        server = FastMCP("policy-integration")
        mw.setup_middleware(server)
        with mock.patch.object(sql_tool, "run_query_async", side_effect=AssertionError("backend must not run")), \
             mock.patch.object(mw.audit, "info"):
            async with Client(server) as client:
                cases = [({}, "SELECT 1", "AUTH_REQUIRED")]
                headers = {"x-ch-user": "alice", "x-ch-password": "fixture"}
                for sql in ("DROP TABLE sales.orders", "SELECT 1; SELECT 2", "SELECT * FROM system.users",
                            "SELECT * FROM url('https://example.com', CSV, 'a String')"):
                    cases.append((headers, sql, "UNSAFE_SQL"))
                for headers, sql, code in cases:
                    with self.subTest(code=code, sql=sql), mock.patch.object(mw, "get_http_headers", return_value=headers):
                        result = await client.call_tool("execute_readonly_sql", {"sql": sql, "purpose": "Inspect data"}, raise_on_error=False)
                        self.assertTrue(result.is_error)
                        self.assertEqual(json.loads(result.content[0].text)["code"], code)


if __name__ == "__main__":
    unittest.main()
