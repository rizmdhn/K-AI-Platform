"""Fresh native account/capability checks; routes never return a database password."""

import asyncio
import os
import re
from urllib.parse import urlsplit
import httpx2 as httpx
from access_approvals import _json
from approval_store import ApprovalError
from tool_errors import GatewayError


async def native_request(authorization, path, method="GET"):
    if (
        not isinstance(authorization, str)
        or len(authorization) > 8192
        or not re.fullmatch(r"Bearer [A-Za-z0-9._~+/-]+={0,2}", authorization, re.I)
    ):
        raise ApprovalError(401, "Platform authentication is required.")
    try:
        origin = os.getenv("KAI_LIBRECHAT_INTERNAL_URL", "http://api:3080")
        parsed = urlsplit(origin)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or not parsed.hostname
            or origin != origin.strip()
            or not (
                parsed.scheme == "https"
                or parsed.scheme == "http"
                and parsed.hostname in ("api", "localhost", "127.0.0.1")
            )
        ):
            raise ValueError()
        async with asyncio.timeout(5):
            async with httpx.AsyncClient(
                timeout=3, trust_env=False, follow_redirects=False
            ) as client:
                async with client.stream(
                    method,
                    origin.rstrip("/") + path,
                    headers={
                        "Authorization": authorization,
                        "Accept": "application/json",
                    },
                    **({"json": {}} if method == "POST" else {}),
                ) as response:
                    if response.status_code in (401, 403, 404):
                        raise ApprovalError(
                            response.status_code, "Platform account access is required."
                        )
                    if response.status_code != 200:
                        raise ValueError()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 16384:
                            raise ValueError()
                        body.extend(chunk)
        return _json(body)
    except ApprovalError:
        raise
    except Exception:
        raise ApprovalError(
            503, "Platform account verification is unavailable."
        ) from None


async def native_context(authorization, subject, write=False):
    if not isinstance(subject, str) or not re.fullmatch(r"[0-9a-f]{24}", subject):
        raise ApprovalError(400, "Invalid platform user.")
    data = await native_request(
        authorization,
        f"/api/admin/users/{subject}/kai-warehouse-context",
        "POST" if write else "GET",
    )
    if (
        not isinstance(data, dict)
        or set(data) != {"actor_id", "target_id", "status", "warehouse"}
        or not isinstance(data["actor_id"], str)
        or not re.fullmatch(r"[0-9a-f]{24}", data["actor_id"])
        or data["target_id"] != subject
        or data["status"] not in ("pending", "active", "existing")
    ):
        raise ApprovalError(503, "Platform account verification is unavailable.")
    ref = data["warehouse"]
    if ref is not None and (
        not isinstance(ref, dict)
        or set(ref) != {"service", "username"}
        or not all(
            isinstance(ref[key], str) and ref[key] and "\x00" not in ref[key]
            for key in ref
        )
        or len(ref["service"]) > 253
        or len(ref["username"].encode()) > 1024
    ):
        raise ApprovalError(503, "Platform account verification is unavailable.")
    return data


async def verify_native_binding(subject, username, service):
    try:
        token = os.getenv("KAI_PLATFORM_INTERNAL_TOKEN", "")
        if not token:
            raise ValueError()
        data = await native_request(
            "Bearer " + token, f"/api/internal/warehouse-user/{subject}"
        )
        ref = data.get("warehouse")
        if (
            data.get("id") != subject
            or data.get("status") not in ("active", "existing")
            or not isinstance(ref, dict)
            or ref.get("username") != username
            or ref.get("service") != urlsplit(service).hostname
        ):
            raise ApprovalError(403, "Platform warehouse reference is unavailable.")
    except ApprovalError as error:
        if error.status in (401, 403, 404):
            raise GatewayError(
                "AUTH_REQUIRED",
                "Your account's warehouse access is inactive or was changed. Contact your administrator.",
            ) from None
        raise GatewayError(
            "DEPENDENCY_UNAVAILABLE", "Platform account verification is unavailable."
        ) from None
    except Exception:
        raise GatewayError(
            "DEPENDENCY_UNAVAILABLE", "Platform account verification is unavailable."
        ) from None
