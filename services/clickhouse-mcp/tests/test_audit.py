import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import per_user_middleware as mw

ALICE = {"x-ch-user": "alice", "x-ch-password": "alice-pw", "x-librechat-user-id": "u1",
         "x-librechat-conversation-id": "{{LIBRECHAT_BODY_CONVERSATIONID}}"}


def run(tool, args, headers, call_next):
    context = SimpleNamespace(message=SimpleNamespace(name=tool, arguments=args))
    state = {}

    async def set_state(key, value, serializable=True):
        state[key] = value

    with mock.patch.object(mw, "get_http_headers", return_value=headers), \
         mock.patch.object(mw, "get_context", return_value=SimpleNamespace(set_state=set_state)), \
         mock.patch.object(mw.audit, "info") as info:
        error = None
        try:
            asyncio.run(mw.PerUserClickHouse().on_call_tool(context, call_next))
        except mw.ToolError as caught:
            error = caught
    info.assert_called_once()
    return json.loads(info.call_args[0][0]), state, error


def returning(rows):
    async def call_next(context):
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"columns": ["a"], "rows": rows}))])
    return call_next


def raising(message):
    async def call_next(context):
        raise mw.ToolError(message)
    return call_next


@mock.patch.dict('os.environ', {'KAI_BINDING_MODE': 'manual'})
class AuditTests(unittest.TestCase):
    def test_success_line_has_trace_fields_and_no_secrets(self):
        sql = "SELECT secret_literal FROM t"
        record, state, error = run("run_query", {"query": sql}, ALICE, returning([[1], [2]]))
        self.assertIsNone(error)
        self.assertEqual(record["outcome"], "ok")
        self.assertEqual(record["rows"], 2)
        self.assertEqual((record["ch_user"], record["librechat_user"]), ("alice", "u1"))
        self.assertIsNone(record["conversation"])  # unresolved placeholder is not logged
        self.assertEqual(len(record["sql_sha256"]), 64)
        line = json.dumps(record)
        for secret in ("alice-pw", "secret_literal", "[1]"):
            self.assertNotIn(secret, line)
        override = state[mw.CLIENT_CONFIG_OVERRIDES_KEY]
        self.assertEqual(override["settings"]["log_comment"], record["request_id"])

    def test_denials_are_logged_with_their_reason(self):
        never = raising("call_next must not run")
        cases = [
            ("run_query", {"query": "SELECT 1"}, {}, never, "no_credentials"),
            ("run_query", {"query": "DROP TABLE t"}, ALICE, never, "unsafe_sql"),
            ("run_query", {"query": "SELECT 1"}, ALICE,
             raising("Query execution failed: Code: 497. DB::Exception: ACCESS_DENIED"), "access_denied"),
            ("list_tables", {"database": "sales"}, ALICE,
             raising("Code: 516. AUTHENTICATION_FAILED"), "auth_failed"),
        ]
        for tool, args, headers, call_next, expected in cases:
            with self.subTest(expected=expected):
                record, _, error = run(tool, args, headers, call_next)
                self.assertIsNotNone(error)
                self.assertEqual(record["outcome"], expected)
                self.assertNotIn("alice-pw", json.dumps(record))


if __name__ == "__main__":
    unittest.main()
