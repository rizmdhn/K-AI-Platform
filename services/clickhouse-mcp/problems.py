"""P6-10: sanitized per-user outcomes for the admin "needs attention" view, and the admin data access log.

Internal token only (the platform api calls it). Reads kai.audit_events (audit_store), never the audit file:
fixed outcome codes, timestamps and counts; never SQL, passwords or result values. Never probes ClickHouse. The
access log adds the ClickHouse username and tool per call, which the admin already sees on the user page.
"""

import asyncio
import hmac
import os
from datetime import datetime, timezone

from starlette.responses import JSONResponse

import audit_store
import database
from binding_preflight import check

LOG_DAYS = {1, 7, 30, 90}
USER, TOOL = audit_store.USER, audit_store.TOOL
CHECKS = (("binding_dsn", "KAI_BINDING_DSN"), ("keyring", "keyring"), ("warehouse_target", "warehouse_target"))


def _authorized(request):
    token = os.getenv("KAI_PLATFORM_INTERNAL_TOKEN", "")
    given = request.headers.get("authorization", "")
    return bool(token) and hmac.compare_digest(given.encode(), f"Bearer {token}".encode())


async def log_request(request):
    if not _authorized(request):
        return JSONResponse({"message": "Unauthorized."}, status_code=401)
    query = request.query_params
    days, user, outcome, tool = query.get("days", "1"), query.get("user", ""), query.get("outcome", ""), query.get("tool", "")
    cursor = query.get("cursor", "")
    if (not days.isdigit() or int(days) not in LOG_DAYS or (user and not USER.fullmatch(user))
            or (outcome and outcome not in audit_store.OUTCOMES) or (tool and not TOOL.fullmatch(tool))
            or len(cursor) > 32):
        return JSONResponse({"message": "Isian tidak valid."}, status_code=400)
    try:
        result = await asyncio.to_thread(audit_store.read_log, os.environ["KAI_CATALOG_DSN"], datetime.now(timezone.utc),
                                         int(days), user, outcome, tool, cursor)
    except Exception:
        return JSONResponse({"version": 1, "available": False}, headers={"Cache-Control": "no-store"})
    return JSONResponse({"version": 1, "available": True, **result}, headers={"Cache-Control": "no-store"})


async def _active_subjects(dsn):
    """Active bound users -> newest credential check, in the audit stamp format (UTC)."""
    async def read(connection):
        cursor = await connection.execute("SELECT librechat_user_id, verified_at FROM kai.active_ch_bindings")
        return await cursor.fetchall()

    subjects = {}
    for row in await database.run_database(dsn, read, timeout=5):
        user, at = row.get("librechat_user_id"), row.get("verified_at")
        if not isinstance(user, str) or not USER.fullmatch(user):
            continue
        stamp = at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(at, datetime) else ""
        subjects[user] = max(subjects.get(user, ""), stamp)
    return subjects


async def problems_request(request):
    if not _authorized(request):
        return JSONResponse({"message": "Unauthorized."}, status_code=401)
    now = datetime.now(timezone.utc)
    result = {"version": 1, "audit_available": False, "database_available": False,
              "calls_24h": 0, "errors_24h": 0, "users": {}, "active_subjects": [], "credentials_verified_at": {}}
    try:
        result.update(await asyncio.to_thread(audit_store.summary, os.environ["KAI_CATALOG_DSN"], now),
                      audit_available=True)
    except Exception:
        pass
    try:
        subjects = await _active_subjects(os.environ["KAI_BINDING_DSN"])
        result.update(active_subjects=sorted(subjects), credentials_verified_at={u: at for u, at in subjects.items() if at},
                      database_available=True)
    except Exception:
        pass
    try:
        checks = await asyncio.to_thread(check)
    except Exception:
        checks = {}
    result["checks"] = {name: checks.get(source) is True for name, source in CHECKS}
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


def register_problem_routes(mcp):
    mcp.custom_route("/v1/internal/problems", methods=["GET"])(problems_request)
    mcp.custom_route("/v1/internal/audit", methods=["GET"])(log_request)
