import json
import unittest
from types import SimpleNamespace
from unittest import mock

from fastmcp import Client, FastMCP
from fastmcp.tools.base import ToolResult

import per_user_middleware as mw


HEADERS = {"x-ch-user": "alice", "x-ch-password": "fixture-password"}


def upstream(rows):
    payload = {"columns": ["value"], "rows": rows}
    return ToolResult(content=json.dumps(payload), structured_content=payload)


@mock.patch.dict('os.environ', {'KAI_BINDING_MODE': 'manual'})
class ResultLimiterMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, result, *, tool="run_query", headers=HEADERS):
        context = SimpleNamespace(message=SimpleNamespace(name=tool, arguments={"query": "SELECT 1"}))
        next_call = mock.AsyncMock(return_value=result)
        state = mock.AsyncMock()
        with mock.patch.object(mw, "get_http_headers", return_value=headers), \
             mock.patch.object(mw, "get_context", return_value=SimpleNamespace(set_state=state)), \
             mock.patch.object(mw.audit, "info") as log:
            returned = await mw.PerUserClickHouse().on_call_tool(context, next_call)
        return returned, json.loads(log.call_args.args[0]), state

    async def test_caps_returned_rows_and_removes_unbounded_structured_copy(self):
        result, audit, state = await self.invoke(upstream([[i] for i in range(1001)]))
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["row_count"], 1000)
        self.assertEqual(payload["rows"][-1], [999])
        self.assertTrue(payload["truncated"])
        self.assertEqual(result.structured_content, {"result": result.content[0].text})
        self.assertEqual(len(result.content), 1)
        self.assertEqual(audit["rows"], 1000)
        self.assertEqual(audit["bytes"], len(result.content[0].text.encode()))
        self.assertTrue(audit["truncated"])
        self.assertNotIn("fixture-password", json.dumps(audit))
        override = state.call_args.args[1]
        self.assertEqual(override["username"], "alice")
        self.assertEqual(override["settings"]["log_comment"], audit["request_id"])

    async def test_caps_bytes_and_does_not_audit_row_values(self):
        result, audit, _ = await self.invoke(upstream([["private-result-雪" * 500]] * 1000))
        self.assertLessEqual(len(result.content[0].text.encode()), 1_048_576)
        self.assertTrue(json.loads(result.content[0].text)["truncated"])
        self.assertNotIn("private-result", json.dumps(audit))

    async def test_oversized_columns_fail_with_safe_error(self):
        result = ToolResult(content=json.dumps({"columns": ["private-column" * 100000], "rows": []}))
        with self.assertRaises(mw.ToolError) as caught:
            await self.invoke(result)
        self.assertIn("QUERY_LIMIT", str(caught.exception))
        self.assertNotIn("private-column", str(caught.exception))

    async def test_unexpected_content_blocks_fail_closed(self):
        result = ToolResult(content=[*upstream([[1]]).content, *upstream([["private-extra"]]).content])
        with self.assertRaises(mw.ToolError) as caught:
            await self.invoke(result)
        self.assertNotIn("private-extra", str(caught.exception))

    async def test_metadata_tool_contract_is_preserved(self):
        original = ToolResult(content='{"tables":[]}', structured_content={"tables": []})
        result, audit, _ = await self.invoke(original, tool="list_tables")
        self.assertIs(result, original)
        self.assertEqual(audit["outcome"], "ok")

    async def test_real_fastmcp_dispatch_returns_only_bounded_output(self):
        server = FastMCP("limiter-integration")
        observed = []

        @server.tool(name="run_query")
        async def query(query: str) -> str:
            observed.append(await mw.get_context().get_state(mw.CLIENT_CONFIG_OVERRIDES_KEY))
            return json.dumps({"columns": ["value"], "rows": [[i] for i in range(1001)]})

        mw.setup_middleware(server)
        with mock.patch.object(mw, "get_http_headers", return_value=HEADERS), \
             mock.patch.object(mw.audit, "info"):
            async with Client(server) as client:
                result = await client.call_tool("run_query", {"query": "SELECT 1"})
                payload = json.loads(result.content[0].text)
                self.assertEqual(len(payload["rows"]), 1000)
                self.assertTrue(payload["truncated"])
                self.assertEqual(result.structured_content, {"result": result.content[0].text})
                denied = await client.call_tool("run_query", {"query": "DROP TABLE x"}, raise_on_error=False)
                self.assertTrue(denied.is_error)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["username"], "alice")


if __name__ == "__main__":
    unittest.main()
