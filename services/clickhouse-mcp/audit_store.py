"""Audit lines -> kai.audit_events, and the queries behind the Admin Panel log and "needs attention" summary.

Writes never slow a tool call: the logging handler only enqueues; one background thread inserts in batches and ages
rows out in bounded deletes. If PostgreSQL is down, rows wait in a bounded queue (oldest dropped and counted) while
audit.jsonl keeps every line. Reads page by id (newest first) so a page costs the same at row 100 or 10 million.
"""

import base64
import json
import logging
import os
import queue
import re
import threading
import time
from datetime import datetime, timedelta, timezone

USER = re.compile(r"[0-9a-f]{24}")
TOOL = re.compile(r"[a-z][a-z0-9_]{0,63}")
OUTCOMES = {"ok", "cancelled", "error", "auth_failed", "login_rejected", "access_denied", "unsafe_sql", "query_limit",
            "no_credentials"}
DENIED = {"access_denied"}  # ClickHouse refused: a grant question for the admin
BLOCKED = {"unsafe_sql"}  # kAI's SQL checker refused before ClickHouse (write/DDL attempts, disallowed SQL)
COLUMNS = ("ts", "librechat_user", "tool", "ch_user", "database", "outcome", "error_code", "request_id", "rows",
           "truncated", "duration_ms")
LIMITS = {"tool": 64, "ch_user": 256, "database": 256, "error_code": 64, "request_id": 64}
QUEUE_SIZE = 20_000
BATCH = 500
RETENTION_EVERY = 3600
RETENTION_CHUNK = 10_000
PAGE = 100
FACETS = 1000
log = logging.getLogger(__name__)


def _connect(dsn, timeout_ms=5000):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(dsn, connect_timeout=3, autocommit=True, row_factory=dict_row, application_name="kai-audit",
                           options=f"-c statement_timeout={timeout_ms}")


def to_row(message, created):
    """One audit JSON line -> insert tuple, or None when it has no platform user (nothing to attribute)."""
    try:
        audit = json.loads(message)
    except (TypeError, ValueError):
        return None
    user = audit.get("librechat_user") if isinstance(audit, dict) else None
    if not isinstance(user, str) or not USER.fullmatch(user):
        return None
    row = {key: audit.get(key) for key in COLUMNS[2:]}
    for key, size in LIMITS.items():
        row[key] = str(row[key])[:size] if row[key] is not None else None
    if row["tool"] and not TOOL.fullmatch(row["tool"]):
        row["tool"] = None
    row["outcome"] = row["outcome"] if row["outcome"] in OUTCOMES else "error"
    for key in ("rows", "duration_ms"):
        row[key] = row[key] if isinstance(row[key], int) and not isinstance(row[key], bool) else None
    row["truncated"] = row["truncated"] if isinstance(row["truncated"], bool) else None
    return (datetime.fromtimestamp(created, timezone.utc), user, *row.values())


class Writer(logging.Handler):
    """Logging handler + one daemon thread: batched inserts and hourly retention."""

    def __init__(self, dsn, retention_days, connect=_connect, start=True, size=QUEUE_SIZE):
        super().__init__()
        self.dsn, self.retention_days, self.connect = dsn, retention_days, connect
        self.queue, self.dropped, self.connection, self.last_retention = queue.Queue(size), 0, None, 0.0
        if start:
            threading.Thread(target=self._run, name="kai-audit-writer", daemon=True).start()

    def emit(self, record):
        row = to_row(record.getMessage(), record.created)
        if row is None:
            return
        while True:
            try:
                self.queue.put_nowait(row)
                return
            except queue.Full:
                try:
                    self.queue.get_nowait()
                    self.dropped += 1  # audit.jsonl still has it
                except queue.Empty:
                    pass

    def _run(self):
        while True:
            batch = [self.queue.get()]
            while len(batch) < BATCH:
                try:
                    batch.append(self.queue.get(timeout=0.5))
                except queue.Empty:
                    break
            self.flush_rows(batch)

    def flush_rows(self, batch):
        for attempt in range(5):
            try:
                if self.connection is None or self.connection.closed:
                    self.connection = self.connect(self.dsn)
                with self.connection.cursor() as cursor:
                    cursor.executemany(f"INSERT INTO kai.audit_events ({','.join(COLUMNS)}) VALUES "
                                       f"({','.join(['%s'] * len(COLUMNS))})", batch)
                self.retain()
                return True
            except Exception:
                self.connection = None
                time.sleep(min(30, 2 ** attempt))
        self.dropped += len(batch)
        log.warning("audit rows not stored in PostgreSQL (kept in audit.jsonl): %s dropped so far", self.dropped)
        return False

    def retain(self, now=time.monotonic):
        """Delete rows past retention in RETENTION_CHUNK slices so a big backlog never holds a long lock."""
        if now() - self.last_retention < RETENTION_EVERY:
            return
        self.last_retention = now()
        days = self.retention_days()
        with self.connection.cursor() as cursor:
            while True:
                cursor.execute("DELETE FROM kai.audit_events WHERE id IN (SELECT id FROM kai.audit_events "
                               "WHERE ts < now() - make_interval(days => %s) LIMIT %s)", (days, RETENTION_CHUNK))
                if cursor.rowcount < RETENTION_CHUNK:
                    break


def attach(logger, dsn, retention_days):
    writer = Writer(dsn, retention_days)
    logger.addHandler(writer)
    return writer


def encode_cursor(row_id):
    return base64.urlsafe_b64encode(str(row_id).encode()).decode().rstrip("=")


def decode_cursor(value):
    """Opaque page token -> row id, or None (invalid tokens restart at the newest page)."""
    try:
        text = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        return int(text) if text.isdigit() else None
    except (ValueError, UnicodeDecodeError):
        return None


def _where(days, user, outcome, tool, now):
    clauses, params = ["ts >= %s"], [now - timedelta(days=days)]
    for column, value in (("librechat_user", user), ("outcome", outcome), ("tool", tool)):
        if value:
            clauses.append(f"{column} = %s")
            params.append(value)
    return clauses, params


_facets = {}


def read_log(dsn, now, days, user="", outcome="", tool="", cursor="", connect=_connect):
    """One page (PAGE rows, newest first) plus counts per outcome over every match and the filter choices."""
    clauses, params = _where(days, user, outcome, tool, now)
    before = decode_cursor(cursor) if cursor else None
    with connect(dsn, timeout_ms=10000) as connection:
        page_clauses = clauses + (["id < %s"] if before else [])
        rows = connection.execute(
            f"SELECT id, {','.join(COLUMNS)} FROM kai.audit_events WHERE {' AND '.join(page_clauses)} "
            "ORDER BY id DESC LIMIT %s", (*params, *([before] if before else []), PAGE + 1)).fetchall()
        counts = {r["outcome"]: r["n"] for r in connection.execute(
            f"SELECT outcome, count(*) AS n FROM kai.audit_events WHERE {' AND '.join(clauses)} GROUP BY outcome",
            params).fetchall()}
        # Filter choices change slowly: cache per period for a minute instead of two range scans on every page.
        cached = _facets.get(days)
        if not cached or time.monotonic() - cached[0] > 60:
            since = now - timedelta(days=days)
            users = [r["librechat_user"] for r in connection.execute(
                "SELECT librechat_user FROM kai.audit_events WHERE ts >= %s GROUP BY 1 LIMIT %s", (since, FACETS))]
            tools = [r["tool"] for r in connection.execute(
                "SELECT tool FROM kai.audit_events WHERE ts >= %s AND tool IS NOT NULL GROUP BY 1 LIMIT 100", (since,))]
            cached = _facets[days] = (time.monotonic(), sorted(users), sorted(tools))
    rows, more = rows[:PAGE], len(rows) > PAGE
    next_cursor = encode_cursor(rows[-1]["id"]) if more else None
    for row in rows:
        row["ts"] = row["ts"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        row["user"] = row.pop("librechat_user")
        row.pop("id")
    return {"rows": rows, "counts": counts, "total": sum(counts.values()), "users": cached[1], "tools": cached[2],
            "next_cursor": next_cursor}


def summary(dsn, now, connect=_connect):
    """Per-user 24 h outcomes for the "needs attention" panel; same shape the platform api already reads."""
    since = now - timedelta(hours=24)
    with connect(dsn) as connection:
        totals = connection.execute(
            "SELECT count(*) AS calls, count(*) FILTER (WHERE outcome <> 'ok') AS errors FROM kai.audit_events "
            "WHERE ts >= %s AND outcome <> 'cancelled'", (since,)).fetchone()
        per_user = connection.execute(
            "SELECT librechat_user AS u, max(ts) FILTER (WHERE outcome = 'ok') AS last_ok, "
            "count(*) FILTER (WHERE outcome <> 'ok') AS errors, "
            "count(*) FILTER (WHERE outcome = ANY(%s)) AS denied, count(*) FILTER (WHERE outcome = ANY(%s)) AS blocked "
            "FROM kai.audit_events WHERE ts >= %s AND outcome <> 'cancelled' GROUP BY 1",
            (sorted(DENIED), sorted(BLOCKED), since)).fetchall()
        last = {r["u"]: r for r in connection.execute(
            "SELECT DISTINCT ON (librechat_user) librechat_user AS u, outcome, ts FROM kai.audit_events "
            "WHERE ts >= %s AND outcome NOT IN ('ok', 'cancelled') ORDER BY librechat_user, ts DESC", (since,))}
    stamp = lambda at: at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if at else None
    users = {r["u"]: {"last_ok": stamp(r["last_ok"]), "errors": r["errors"], "denied": r["denied"],
                      "blocked": r["blocked"],
                      "last_error": {"outcome": last[r["u"]]["outcome"], "at": stamp(last[r["u"]]["ts"])} if r["u"] in last else None}
             for r in per_user}
    return {"calls_24h": totals["calls"], "errors_24h": totals["errors"], "users": users}
