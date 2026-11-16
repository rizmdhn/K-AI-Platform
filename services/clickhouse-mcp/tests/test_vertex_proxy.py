"""Vertex express-key adapter without network: auth, header swap, fixed URL, model prefix, pass-through."""
import asyncio
import json
import os
import unittest
from unittest import mock

import httpx2 as httpx

import vertex_proxy

ENV = {"KAI_PLATFORM_INTERNAL_TOKEN": "internal", "KAI_VERTEX_API_KEY": "AQ.fixture",
       "KAI_VERTEX_PROJECT_ID": "client-project-1", "KAI_VERTEX_LOCATION": ""}


class Request:
    def __init__(self, body, token="internal"):
        self.headers = {"authorization": f"Bearer {token}", "accept": "text/event-stream"}
        self._body = json.dumps(body).encode() if not isinstance(body, bytes) else body

    async def body(self):
        return self._body


def run(request, handler=None):
    seen = {}

    def reply(upstream):
        seen.update(url=str(upstream.url), headers=dict(upstream.headers), body=json.loads(upstream.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"data: {\"ok\":true}\n\n")

    async def call():
        response = await vertex_proxy.chat_completions(request)
        chunks = [chunk async for chunk in response.body_iterator] if hasattr(response, "body_iterator") else []
        if getattr(response, "background", None):
            await response.background()
        return response, b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks)

    with mock.patch.object(vertex_proxy, "_transport", httpx.MockTransport(handler or reply)):
        response, body = asyncio.run(call())
    return response, body, seen


class VertexProxyTests(unittest.TestCase):
    def test_key_goes_in_x_goog_api_key_and_never_as_bearer(self):
        with mock.patch.dict(os.environ, ENV):
            response, body, seen = run(Request({"model": "gemini-3.5-flash", "stream": True, "messages": []}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, b"data: {\"ok\":true}\n\n")
        self.assertEqual(seen["url"], "https://aiplatform.googleapis.com/v1/projects/client-project-1/locations/global"
                                      "/endpoints/openapi/chat/completions")
        self.assertEqual(seen["headers"]["x-goog-api-key"], "AQ.fixture")
        self.assertNotIn("authorization", seen["headers"])
        self.assertEqual(seen["body"]["model"], "google/gemini-3.5-flash")

    def test_regional_host_and_explicit_publisher_kept(self):
        with mock.patch.dict(os.environ, {**ENV, "KAI_VERTEX_LOCATION": "asia-southeast2"}):
            _, _, seen = run(Request({"model": "google/gemini-3.5-flash", "messages": []}))
        self.assertTrue(seen["url"].startswith("https://asia-southeast2-aiplatform.googleapis.com/v1/projects/client-project-1/locations/asia-southeast2/"))
        self.assertEqual(seen["body"]["model"], "google/gemini-3.5-flash")

    def test_refuses_without_internal_token_or_configuration(self):
        with mock.patch.dict(os.environ, ENV):
            self.assertEqual(run(Request({"model": "m"}, token="wrong"))[0].status_code, 401)
            self.assertEqual(run(Request(b"not json"))[0].status_code, 400)
        for missing in ("KAI_VERTEX_API_KEY", "KAI_VERTEX_PROJECT_ID"):
            with self.subTest(missing=missing), mock.patch.dict(os.environ, {**ENV, missing: ""}):
                self.assertEqual(run(Request({"model": "m"}))[0].status_code, 503)
        with mock.patch.dict(os.environ, {**ENV, "KAI_VERTEX_PROJECT_ID": "../evil"}):
            self.assertEqual(run(Request({"model": "m"}))[0].status_code, 503)

    def test_vertex_errors_pass_through_without_the_key(self):
        def denied(upstream):
            return httpx.Response(403, json={"error": {"message": "Permission denied"}})

        with mock.patch.dict(os.environ, ENV):
            response, body, _ = run(Request({"model": "m"}), denied)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"AQ.fixture", body)


class GeminiSchemaTests(unittest.TestCase):
    def test_tool_schemas_are_made_gemini_compatible_without_losing_fields(self):
        schema = {"additionalProperties": False, "type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "minLength": 1},
            "params": {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}], "default": None},
            "page_size": {"type": "integer", "exclusiveMinimum": 0, "default": 50, "description": "rows"},
            "default": {"type": "string", "title": "Default"},
            "mode": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}}
        clean = vertex_proxy.gemini_schema(schema)
        self.assertEqual(clean["required"], ["query"])
        self.assertEqual(set(clean["properties"]), {"query", "params", "page_size", "default", "mode"})
        self.assertEqual(clean["properties"]["params"], {"type": "object"})
        self.assertEqual(clean["properties"]["page_size"], {"type": "integer", "description": "rows"})
        self.assertEqual(clean["properties"]["default"], {"type": "string"})
        self.assertEqual(clean["properties"]["query"]["minLength"], 1)
        self.assertIn("anyOf", clean["properties"]["mode"])  # real unions are left alone
        self.assertNotIn("additionalProperties", json.dumps(clean))

    def test_proxy_rewrites_tools_and_logs_upstream_rejections(self):
        body = {"model": "gemini-x", "messages": [{"role": "user", "content": "rahasia pengguna"}],
                "tools": [{"type": "function", "function": {"name": "run_query", "parameters": {"type": "object", "additionalProperties": False, "properties": {}}}}]}
        seen = {}

        def reject(upstream):
            seen["body"] = json.loads(upstream.content)
            return httpx.Response(400, headers={"content-type": "application/json"}, content=b'{"error":{"message":"Invalid tools[0]"}}')

        with mock.patch.dict(os.environ, ENV), self.assertLogs("uvicorn.error", "WARNING") as logs:
            response, _, _ = run(Request(body), reject)
        self.assertNotIn("additionalProperties", json.dumps(seen["body"]["tools"]))
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Invalid tools[0]", response.body)
        text = " ".join(logs.output)
        self.assertIn("HTTP 400", text); self.assertIn("Invalid tools[0]", text)
        self.assertNotIn("AQ.fixture", text); self.assertNotIn("rahasia", text)


class ThoughtSignatureTests(unittest.TestCase):
    def test_follow_up_turn_gets_a_signature_on_the_first_tool_call_only_and_real_ones_are_kept(self):
        body = {"model": "gemini-3.5-flash", "messages": [
            {"role": "user", "content": "data apa saja"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "list_databases", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "list_tables", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "a", "content": "[]"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c", "type": "function", "function": {"name": "run_query", "arguments": "{}"},
                 "extra_content": {"google": {"thought_signature": "real-signature"}}}]}]}
        with mock.patch.dict(os.environ, ENV):
            _, _, seen = run(Request(body))
        messages = seen["body"]["messages"]
        self.assertEqual(messages[1]["tool_calls"][0]["extra_content"]["google"]["thought_signature"], vertex_proxy.SKIP_SIGNATURE)
        self.assertNotIn("extra_content", messages[1]["tool_calls"][1])
        self.assertEqual(messages[3]["tool_calls"][0]["extra_content"]["google"]["thought_signature"], "real-signature")
        self.assertNotIn("extra_content", messages[0])


if __name__ == "__main__":
    unittest.main()
