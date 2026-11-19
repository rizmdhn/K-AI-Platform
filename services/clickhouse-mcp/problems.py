"""P6-10: sanitized per-user outcomes for the admin "needs attention" view, and the admin data access log.

Internal token only (the platform api calls it). Returns fixed outcome codes, timestamps and counts from the
audit file; never SQL, passwords or result values. Never probes ClickHouse. The access log adds the ClickHouse
username and tool per call, which the admin already sees on the user page.
"""

import asyncio
import glob
import hmac
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from starlette.responses import JSONResponse

import database
from binding_preflight import check

WINDOW = timedelta(hours=24)
MAX_BYTES = 20 * 1024 * 1024  # ponytail: newest 20 MB of audit per scan; aggregate in Postgres if traffic outgrows it
OUTCOMES = {"error", "auth_failed", "login_rejected", "access_denied", "unsafe_sql", "query_limit", "no_credentials"}
DENIED = {"access_denied"}  # ClickHouse refused: a grant question for the admin
BLOCKED = {"unsafe_sql"}  # kAI's SQL checker refused before ClickHouse (write/DDL attempts, disallowed SQL)
LOG_DAYS = {1, 7, 30, 90}
LOG_ROWS = 500
LOG_SCAN_BYTES = 200 * 1024 * 1024  # ponytail: whole-file scan per request; index in Postgres if audit grows past this
LOG_OUTCOMES = OUTCOMES | {"ok", "cancelled"}
LOG_FIELDS = ("request_id", "tool", "ch_user", "database", "outcome", "error_code", "rows", "truncated", "duration_ms")
TOOL = re.compile(r"[a-z][a-z0-9_]{0,63}")
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
        item = users.setdefault(user, {"last_ok": None, "last_error": None, "errors": 0, "denied": 0, "blocked": 0})
        if outcome == "ok":
            item["last_ok"] = max(item["last_ok"] or "", stamp)
            continue
        outcome = outcome if outcome in OUTCOMES else "error"
        errors += 1
        item["errors"] += 1
        item["denied"] += outcome in DENIED
        item["blocked"] += outcome in BLOCKED
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


def read_log(directory, now, days, user="", outcome="", tool=""):
    """Newest-first calls in the last `days` days matching the filters: at most LOG_ROWS rows, counts over all matches."""
    since = now - timedelta(days=days)
    files = sorted(glob.glob(os.path.join(directory, "audit.jsonl*")), key=os.path.getmtime, reverse=True)
    if not files:
        raise FileNotFoundError()
    rows, counts, tools, people, budget, partial = [], Counter(), set(), set(), LOG_SCAN_BYTES, False
    for path in files:
        if os.path.getmtime(path) < since.timestamp():
            break
        size = os.path.getsize(path)
        if size > budget:
            partial = True
            break
        budget -= size
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
        for line in reversed(lines):
            try:
                entry = json.loads(line)
                stamp, audit = entry["ts"], entry["audit"]
                at = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                who = audit.get("librechat_user")
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if at < since or not isinstance(who, str) or not USER.fullmatch(who):
                continue
            row = {"ts": stamp, "user": who, **{key: audit.get(key) for key in LOG_FIELDS}}
            if row["outcome"] not in LOG_OUTCOMES:
                row["outcome"] = "error"
            if not isinstance(row["tool"], str) or not TOOL.fullmatch(row["tool"]):
                row["tool"] = None
            if row["tool"]:
                tools.add(row["tool"])
            people.add(who)
            if (user and who != user) or (outcome and row["outcome"] != outcome) or (tool and row["tool"] != tool):
                continue
            counts[row["outcome"]] += 1
            if len(rows) < LOG_ROWS:
                rows.append(row)
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return {"rows": rows, "counts": dict(counts), "total": sum(counts.values()), "tools": sorted(tools),
            "users": sorted(people), "partial": partial}


def _authorized(request):
    token = os.getenv("KAI_PLATFORM_INTERNAL_TOKEN", "")
    given = request.headers.get("authorization", "")
    return bool(token) and hmac.compare_digest(given.encode(), f"Bearer {token}".encode())


async def log_request(request):
    if not _authorized(request):
        return JSONResponse({"message": "Unauthorized."}, status_code=401)
    query = request.query_params
    days, user, outcome, tool = query.get("days", "1"), query.get("user", ""), query.get("outcome", ""), query.get("tool", "")
    if (not days.isdigit() or int(days) not in LOG_DAYS or (user and not USER.fullmatch(user))
            or (outcome and outcome not in LOG_OUTCOMES) or (tool and not TOOL.fullmatch(tool))):
        return JSONResponse({"message": "Isian tidak valid."}, status_code=400)
    try:
        result = await asyncio.to_thread(read_log, os.environ["KAI_AUDIT_LOG_DIR"], datetime.now(timezone.utc),
                                         int(days), user, outcome, tool)
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
        result.update(await asyncio.to_thread(read_audit, os.environ["KAI_AUDIT_LOG_DIR"], now), audit_available=True)
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
