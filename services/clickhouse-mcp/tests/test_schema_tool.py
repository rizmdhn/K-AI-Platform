import json
import unittest
from unittest import mock

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

import per_user_middleware as mw
import schema_tool as schema


def table_record(name="orders", database="sales"):
    return {"database": database, "name": name, "engine": "MergeTree",
            "create_table_query": "PRIVATE_DDL", "engine_full": "PRIVATE_ENGINE_CONFIG",
            "total_rows": 12345, "columns": [
                {"name": "region", "column_type": "String", "comment": "Region"},
                {"name": "amount", "column_type": "UInt32", "comment": ""}]}


@mock.patch.dict('os.environ', {'KAI_BINDING_MODE': 'manual'})
class SchemaToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_schema_without_global_statistics_or_ddl(self):
        with mock.patch.object(schema, "list_tables_async", mock.AsyncMock(
                return_value=json.dumps({"tables": [table_record()]}))):
            text = await schema.get_table_schema("sales", "orders")
        self.assertEqual(json.loads(text), {"database": "sales", "table": "orders", "engine": "MergeTree",
                         "columns": [{"name": "region", "type": "String", "comment": "Region"},
                                     {"name": "amount", "type": "UInt32", "comment": ""}]})
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("12345", text)

    async def test_escapes_like_metacharacters_and_requires_exact_match(self):
        name = "order_%\\archive"
        with mock.patch.object(schema, "list_tables_async", mock.AsyncMock(
                return_value=json.dumps({"tables": [table_record(name)]}))) as lookup:
            result = json.loads(await schema.get_table_schema("sales", name))
        self.assertEqual(result["table"], name)
        self.assertEqual(lookup.call_args.kwargs["like"], "order\\_\\%\\\\archive")

    async def test_missing_and_inaccessible_use_same_error(self):
        for tables in ([], [table_record("other")], [table_record(database="other")]):
            with self.subTest(tables=tables), mock.patch.object(schema, "list_tables_async", mock.AsyncMock(
                    return_value=json.dumps({"tables": tables}))):
                with self.assertRaisesRegex(ToolError, "Table not found or not accessible"):
                    await schema.get_table_schema("sales", "orders")

    async def test_invalid_identifiers_are_rejected_before_lookup(self):
        for database, table in (("", "orders"), ("sales", "  "), ("system", "users"),
                                ("sales", "bad\x00name"), ("x" * 257, "orders")):
            with self.subTest(database=database, table=table), mock.patch.object(
                    schema, "list_tables_async", mock.AsyncMock(side_effect=AssertionError("must not query"))):
                with self.assertRaises(ToolError):
                    await schema.get_table_schema(database, table)

    async def test_oversized_schema_fails_without_echoing_metadata(self):
        record = table_record()
        record["columns"][0]["comment"] = "private-comment" * 100000
        with mock.patch.object(schema, "list_tables_async", mock.AsyncMock(
                return_value=json.dumps({"tables": [record]}))):
            with self.assertRaises(ToolError) as caught:
                await schema.get_table_schema("sales", "orders")
        self.assertIn("QUERY_LIMIT", str(caught.exception))
        self.assertNotIn("private-comment", str(caught.exception))

    async def test_registered_tool_dispatch_has_request_credentials_and_denies_missing(self):
        server = FastMCP("schema-integration")
        mw.setup_middleware(server)
        observed = []

        async def lookup(*args, **kwargs):
            observed.append(await mw.get_context().get_state(mw.CLIENT_CONFIG_OVERRIDES_KEY))
            return json.dumps({"tables": [table_record()]})

        with mock.patch.object(schema, "list_tables_async", side_effect=lookup), \
             mock.patch.object(mw.audit, "info") as audit:
            async with Client(server) as client:
                with mock.patch.object(mw, "get_http_headers", return_value={"x-ch-user": "alice", "x-ch-password": "fixture"}):
                    result = await client.call_tool("get_table_schema", {"database": "sales", "table": "orders"})
                    self.assertEqual(json.loads(result.content[0].text)["columns"][0]["name"], "region")
                with mock.patch.object(mw, "get_http_headers", return_value={}):
                    result = await client.call_tool("get_table_schema", {"database": "sales", "table": "orders"}, raise_on_error=False)
                    self.assertTrue(result.is_error)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["username"], "alice")
        records = [json.loads(call.args[0]) for call in audit.call_args_list]
        self.assertEqual([r["outcome"] for r in records], ["ok", "no_credentials"])


if __name__ == "__main__":
    unittest.main()
