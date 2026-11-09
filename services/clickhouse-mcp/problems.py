"""P6-10: sanitized per-user outcomes for the admin "needs attention" view.

Internal token only (the platform api calls it). Returns fixed outcome codes, timestamps and counts from the
audit file; never SQL, usernames, passwords or result values. Never probes ClickHouse.
"""

import asyncio
import glob
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone

from starlette.responses import JSONResponse

import database
from binding_preflight import check

WINDOW = timedelta(hours=24)
MAX_BYTES = 20 * 1024 * 1024  # ponytail: newest 20 MB of audit per scan; aggregate in Postgres if traffic outgrows it
OUTCOMES = {"error", "auth_failed", "login_rejected", "access_denied", "unsafe_sql", "query_limit", "no_credentials"}
DENIED = {"access_denied", "unsafe_sql"}
USER = re.compile(r"[0-9a-f]{24}")
CHECKS = (("binding_dsn", "KAI_BINDING_DSN"), ("keyring", "keyring"), ("warehouse_target", "warehouse_target"))


def summarize(lines, now):
    users, calls, errors = {}, 0, 0
    for line in lines:
        try:
            entry = json.loads(line)
            stamp, audit = entry["ts"], entry["audit"]
            at = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (ValueError, KeyError, TypeError):
            continue
        user = audit.get("librechat_user") if isinstance(audit, dict) else None
        if at < now - WINDOW or not isinstance(user, str) or not USER.fullmatch(user):
            continue
        outcome = audit.get("outcome")
        if outcome == "cancelled":
            continue
        calls += 1
        item = users.setdefault(user, {"last_ok": None, "last_error": None, "errors": 0, "denied": 0})
        if outcome == "ok":
            item["last_ok"] = max(item["last_ok"] or "", stamp)
            continue
        outcome = outcome if outcome in OUTCOMES else "error"
        errors += 1
        item["errors"] += 1
        item["denied"] += outcome in DENIED
        if not item["last_error"] or stamp >= item["last_error"]["at"]:
            item["last_error"] = {"outcome": outcome, "at": stamp}
    return {"calls_24h": calls, "errors_24h": errors, "users": users}


def read_audit(directory, now):
    files = sorted(glob.glob(os.path.join(directory, "audit.jsonl*")), key=os.path.getmtime)[-2:]
    if not files:
        raise FileNotFoundError()
    chunks, budget = [], MAX_BYTES
    for path in reversed(files):
        with open(path, "rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - budget))
            chunks.insert(0, handle.read(budget))
            budget -= min(size, budget)
        if budget <= 0:
            break
    return summarize(b"".join(chunks).decode("utf-8", "replace").splitlines(), now)


async def _active_subjects(dsn):
    async def read(connection):
        cursor = await connection.execute("SELECT librechat_user_id FROM kai.active_ch_bindings")
        return await cursor.fetchall()

    rows = await database.run_database(dsn, read, timeout=5)
    return sorted({r["librechat_user_id"] for r in rows if isinstance(r.get("librechat_user_id"), str) and USER.fullmatch(r["librechat_user_id"])})


async def problems_request(request):
    token = os.getenv("KAI_PLATFORM_INTERNAL_TOKEN", "")
    given = request.headers.get("authorization", "")
    if not token or not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
        return JSONResponse({"message": "Unauthorized."}, status_code=401)
    now = datetime.now(timezone.utc)
    result = {"version": 1, "audit_available": False, "database_available": False,
              "calls_24h": 0, "errors_24h": 0, "users": {}, "active_subjects": []}
    try:
        result.update(await asyncio.to_thread(read_audit, os.environ["KAI_AUDIT_LOG_DIR"], now), audit_available=True)
    except Exception:
        pass
    try:
        result.update(active_subjects=await _active_subjects(os.environ["KAI_BINDING_DSN"]), database_available=True)
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
