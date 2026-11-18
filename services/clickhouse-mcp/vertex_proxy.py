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
import logging
import os
import re
from collections import OrderedDict

import httpx2 as httpx
from starlette.background import BackgroundTask
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

MAX_BODY = 20 * 1024 * 1024  # chat history with document excerpts
PROJECT = re.compile(r"[a-z][a-z0-9-]{4,61}[a-z0-9]")
LOCATION = re.compile(r"[a-z0-9-]{1,40}")
TIMEOUT = httpx.Timeout(300.0, connect=10.0)  # long answers stream for minutes
_transport = None  # tests inject a mock transport
log = logging.getLogger("uvicorn.error")
# Documented placeholder for clients that do not keep Gemini's thought signatures.
SKIP_SIGNATURE = "skip_thought_signature_validator"


# Real thought signatures from Vertex replies, by tool call id. LibreChat drops them from the history it sends back.
# ponytail: in-memory per proxy process (lost on restart, then the documented placeholder is used); a shared store if
# the proxy ever runs as several replicas.
MAX_SIGNATURES = 10_000
_signatures = OrderedDict()


def remember(call):
    """Store the signature Vertex attached to a tool call (only the first call of a step carries one)."""
    if not isinstance(call, dict) or not isinstance(call.get("id"), str):
        return
    google = (call.get("extra_content") or {}).get("google") if isinstance(call.get("extra_content"), dict) else None
    signature = google.get("thought_signature") if isinstance(google, dict) else None
    if isinstance(signature, str) and signature:
        _signatures[call["id"]] = signature
        _signatures.move_to_end(call["id"])
        while len(_signatures) > MAX_SIGNATURES:
            _signatures.popitem(last=False)


class SignatureTap:
    """Reads tool-call signatures out of the reply while it is passed through unchanged."""

    def __init__(self):
        self.buffer = b""

    def feed(self, chunk):
        self.buffer += chunk
        *lines, self.buffer = self.buffer.split(b"\n")
        for line in lines:
            self._line(line.strip())
        if len(self.buffer) > MAX_BODY:  # a reply without newlines (non-streaming) is parsed at close
            self.buffer = b""

    def close(self):
        self._line(self.buffer.strip())
        self.buffer = b""

    def _line(self, line):
        if line.startswith(b"data:"):
            line = line[5:].strip()
        if not line.startswith(b"{"):
            return
        try:
            data = json.loads(line)
        except ValueError:
            return
        for choice in data.get("choices") or [] if isinstance(data, dict) else []:
            part = (choice.get("delta") or choice.get("message") or {}) if isinstance(choice, dict) else {}
            for call in part.get("tool_calls") or [] if isinstance(part, dict) else []:
                remember(call)


def keep_tool_turns_valid(messages):
    """Gemini 3 rejects (HTTP 400) a turn whose earlier tool call lost its thought signature; LibreChat drops it.

    The real signature remembered from the reply is put back; the documented placeholder (which skips validation and
    can cost reasoning quality on multi-step tool use) is only used when it is unknown, e.g. after a proxy restart.
    """
    for message in messages if isinstance(messages, list) else []:
        calls = message.get("tool_calls") if isinstance(message, dict) and message.get("role") == "assistant" else None
        if not (isinstance(calls, list) and calls and isinstance(calls[0], dict)):
            continue
        extra = calls[0].setdefault("extra_content", {})  # only the first call of a step carries the signature
        google = extra.setdefault("google", {}) if isinstance(extra, dict) else None
        if isinstance(google, dict):
            google.setdefault("thought_signature", _signatures.get(calls[0].get("id"), SKIP_SIGNATURE))


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
    # Tool schemas pass through unchanged: Gemini accepts them, and LibreChat validates the model's arguments
    # against the same schema (dropping additionalProperties here let the model add arguments LibreChat rejects).
    keep_tool_turns_valid(payload.get("messages"))
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

    if upstream.status_code >= 400:
        # Vertex explains rejections in a small JSON body; log it (never the key or the request) and pass it on.
        detail = (await upstream.aread())[:4096]
        await close()
        log.warning("Vertex AI answered HTTP %s for model %s: %s", upstream.status_code, payload["model"],
                    detail[:600].decode("utf-8", "replace").replace("\n", " "))
        return Response(detail, status_code=upstream.status_code,
                        media_type=upstream.headers.get("content-type", "application/json"))

    async def passthrough():
        tap = SignatureTap()
        async for chunk in upstream.aiter_bytes():
            tap.feed(chunk)
            yield chunk
        tap.close()

    return StreamingResponse(passthrough(), status_code=upstream.status_code,
                             media_type=upstream.headers.get("content-type", "application/json"),
                             background=BackgroundTask(close))


async def health(request):
    return PlainTextResponse("ok")


app = Starlette(routes=[Route("/v1/chat/completions", chat_completions, methods=["POST"]),
                        Route("/healthz", health, methods=["GET"])])
