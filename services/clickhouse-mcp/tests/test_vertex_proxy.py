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


if __name__ == "__main__":
    unittest.main()
