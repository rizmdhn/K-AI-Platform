import json
import unittest
from types import SimpleNamespace
from unittest import mock

import httpx2 as httpx
from fastmcp import Client, FastMCP
from fastmcp.tools.base import ToolResult
from starlette.responses import PlainTextResponse

import per_user_middleware as mw
import health_routes
from tool_errors import GatewayError


@mock.patch.dict('os.environ', {'KAI_BINDING_MODE': 'manual'})
class ErrorBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_argument_errors_are_not_dependency_failures(self):
        server = FastMCP("validation-integration")
        mw.setup_middleware(server)
        with mock.patch.object(mw, "get_http_headers", return_value={"x-ch-user": "alice", "x-ch-password": "fixture"}), \
             mock.patch.object(mw.audit, "info"):
            async with Client(server) as client:
                for args in ({"database": "sales"}, {"database": 12, "table": "orders"}):
                    with self.subTest(args=args):
                        result = await client.call_tool("get_table_schema", args, raise_on_error=False)
                        self.assertTrue(result.is_error)
                        payload = json.loads(result.content[0].text)
                        self.assertEqual(payload["code"], "INVALID_ARGUMENT")
                        self.assertNotIn("12", payload["message"])

    async def invoke(self, error, *, headers=None, tool="run_query", query="SELECT 1", result=None):
        headers = {"x-ch-user": "alice", "x-ch-password": "secret-password"} if headers is None else headers
        context = SimpleNamespace(message=SimpleNamespace(name=tool, arguments={"query": query}))
        async def call_next(context):
            if error is not None:
                raise error
            return result
        with mock.patch.object(mw, "get_http_headers", return_value=headers), \
             mock.patch.object(mw, "get_context", return_value=SimpleNamespace(set_state=mock.AsyncMock())), \
             mock.patch.object(mw.audit, "info") as log:
            with self.assertRaises(mw.ToolError) as caught:
                await mw.PerUserClickHouse().on_call_tool(context, call_next)
        return json.loads(str(caught.exception)), json.loads(log.call_args.args[0])

    async def test_raw_backend_errors_are_safe_and_correlated(self):
        for error, code, outcome in [
            (mw.ToolError("ACCESS_DENIED secret-password SELECT private_rows"), "ACCESS_DENIED", "access_denied"),
            (mw.ToolError("AUTHENTICATION_FAILED secret-password"), "AUTH_REQUIRED", "auth_failed"),
            (mw.ToolError("MEMORY_LIMIT_EXCEEDED private_rows"), "QUERY_LIMIT", "query_limit"),
            (RuntimeError("driver broke secret-password private_rows"), "DEPENDENCY_UNAVAILABLE", "error"),
        ]:
            with self.subTest(code=code):
                payload, audit = await self.invoke(error)
                self.assertEqual(payload["code"], code)
                self.assertEqual(payload["request_id"], audit["request_id"])
                self.assertEqual(audit["error_code"], code)
                self.assertEqual(audit["outcome"], outcome)
                for private in ("secret-password", "private_rows", "SELECT"):
                    self.assertNotIn(private, json.dumps(payload))
                    self.assertNotIn(private, json.dumps(audit))

    async def test_owned_errors_keep_safe_messages(self):
        payload, audit = await self.invoke(GatewayError("INVALID_ARGUMENT", "Provide a database and table."))
        self.assertEqual(payload["message"], "Provide a database and table.")
        self.assertEqual(payload["code"], "INVALID_ARGUMENT")

    async def test_auth_and_sql_denials_have_stable_codes(self):
        for headers, query, code in [({}, "SELECT 1", "AUTH_REQUIRED"),
                                     ({"x-ch-user": "alice", "x-ch-password": "x"}, "DROP TABLE t", "UNSAFE_SQL")]:
            with self.subTest(code=code):
                payload, audit = await self.invoke(AssertionError("must not call backend"), headers=headers, query=query)
                self.assertEqual(payload["code"], code)
                self.assertEqual(payload["request_id"], audit["request_id"])

    async def test_error_result_is_classified_before_json_limiting(self):
        result = ToolResult(content="ACCESS_DENIED secret-password", is_error=True)
        payload, audit = await self.invoke(None, result=result)
        self.assertEqual(payload["code"], "ACCESS_DENIED")
        self.assertNotIn("secret-password", json.dumps(payload))


class HealthRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_routes_distinguish_live_from_dependency_ready(self):
        server = FastMCP("health-integration")
        mw.setup_middleware(server)
        transport = httpx.ASGITransport(app=server.http_app())
        with mock.patch.object(health_routes, "health_check", mock.AsyncMock(
                return_value=PlainTextResponse("Dependency unavailable", status_code=503))) as probe:
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
                live = await client.get("/health/live")
                self.assertEqual(live.status_code, 200)
                self.assertEqual(live.text, "OK")
                probe.assert_not_awaited()
                ready = await client.get("/health/ready")
                self.assertEqual(ready.status_code, 503)
                self.assertEqual(ready.text, "Dependency unavailable")
                probe.return_value = PlainTextResponse("OK")
                ready = await client.get("/health/ready")
                self.assertEqual(ready.status_code, 200)


if __name__ == "__main__":
    unittest.main()
