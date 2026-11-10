"""Gemini on Vertex AI with an express-mode API key, for OpenAI-compatible clients (LibreChat custom endpoint).

Vertex's OpenAI-compatible endpoint accepts an express key only in the x-goog-api-key header and rejects it as
`Authorization: Bearer` (401 "API keys are not supported", tested 10 Nov 2026) - and every OpenAI client sends
Bearer. This route takes the client's Bearer (the platform internal token), drops it, adds the key and forwards
to a fixed Vertex URL built from the project and region (the path cannot be chosen by the caller). The reply,
including streaming, is passed through. The key comes from .env and is never logged or returned.

Runs as its own container (vertex-proxy, same image as the MCP): `uvicorn vertex_proxy:app`. Chat keeps working
while the MCP restarts, and the MCP (which holds ClickHouse credentials) has no route to the model provider.
"""

import hmac
import json
import os
import re

import httpx2 as httpx
from starlette.background import BackgroundTask
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route

MAX_BODY = 20 * 1024 * 1024  # chat history with document excerpts
PROJECT = re.compile(r"[a-z][a-z0-9-]{4,61}[a-z0-9]")
LOCATION = re.compile(r"[a-z0-9-]{1,40}")
TIMEOUT = httpx.Timeout(300.0, connect=10.0)  # long answers stream for minutes
_transport = None  # tests inject a mock transport


def target(env=os.environ):
    """(url, key) or None when Vertex is not configured."""
    key = env.get("KAI_VERTEX_API_KEY", "").strip()
    project = env.get("KAI_VERTEX_PROJECT_ID", "").strip()
    location = env.get("KAI_VERTEX_LOCATION", "").strip() or "global"
    if not key or not PROJECT.fullmatch(project) or not LOCATION.fullmatch(location):
        return None
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    return f"https://{host}/v1/projects/{project}/locations/{location}/endpoints/openapi/chat/completions", key


def _error(status, message):
    return JSONResponse({"error": {"message": message}}, status_code=status)


async def chat_completions(request):
    token = os.getenv("KAI_PLATFORM_INTERNAL_TOKEN", "")
    given = request.headers.get("authorization", "")
    if not token or not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
        return _error(401, "Unauthorized.")
    configured = target()
    if not configured:
        return _error(503, "Vertex AI belum dikonfigurasi (KAI_VERTEX_API_KEY, KAI_VERTEX_PROJECT_ID).")
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return _error(413, "Request too large.")
    try:
        payload = json.loads(raw)
    except ValueError:
        return _error(400, "Invalid JSON.")
    if not isinstance(payload, dict) or not isinstance(payload.get("model"), str):
        return _error(400, "Missing model.")
    if "/" not in payload["model"]:
        payload["model"] = f"google/{payload['model']}"  # Vertex names publisher models google/<id>
    url, key = configured
    client = httpx.AsyncClient(timeout=TIMEOUT, transport=_transport, follow_redirects=False, trust_env=False)
    try:
        upstream = await client.send(client.build_request(
            "POST", url, json=payload,
            headers={"x-goog-api-key": key, "Accept": request.headers.get("accept", "application/json")}), stream=True)
    except httpx.HTTPError as error:
        await client.aclose()
        return _error(502, f"Vertex AI tidak bisa dihubungi ({type(error).__name__}).")

    async def close():
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(upstream.aiter_bytes(), status_code=upstream.status_code,
                             media_type=upstream.headers.get("content-type", "application/json"),
                             background=BackgroundTask(close))


async def health(request):
    return PlainTextResponse("ok")


app = Starlette(routes=[Route("/v1/chat/completions", chat_completions, methods=["POST"]),
                        Route("/healthz", health, methods=["GET"])])
