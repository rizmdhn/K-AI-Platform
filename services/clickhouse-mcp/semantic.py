"""Semantic layer: approved metric definitions and verified queries the CH agent reuses instead of inventing SQL.

Rows live in kai.semantic_definitions (kai_catalog role, KAI_CATALOG_DSN). Drafts come from the agent
(propose_semantic_definition, validated against the proposer's own ClickHouse view) or the Admin Panel; only
'approved' rows reach search_database_metadata, and only for callers whose grants cover every referenced table.
SQL is never run from here on anyone's behalf: the agent executes it with execute_readonly_sql as the caller, so
grants and row policies still apply. Field names follow OpenMetadata (see db/migrations/012) for a later sync.
"""

import asyncio
import json
import os
import re
import time

import sqlglot
from fastmcp.server.dependencies import get_http_headers
from mcp_clickhouse.mcp_server import run_query_async
from sqlglot import exp
from starlette.responses import JSONResponse

from integration_settings import SettingsError, _authorized, _body, _connect
from schema_tool import get_table_schema
from sql_policy import UnsafeSQL
from sql_policy import validate as validate_sql
from tool_errors import GatewayError

KINDS = ("metric", "verified_query")
STATUSES = ("draft", "approved", "rejected")
METRIC_TYPES = ("sum", "count", "count_distinct", "average", "min", "max", "ratio", "other")
NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
TABLE = re.compile(r"([A-Za-z0-9_-]{1,128})\.([A-Za-z0-9_-]{1,128})")
USER = re.compile(r"[0-9a-f]{24}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MAX_TEXT, MAX_EXPRESSION, MAX_SQL, MAX_LIST = 500, 1000, 4000, 30
MAX_DRAFTS = 1000  # caps agent drafts waiting for review, so a runaway conversation cannot flood the queue
CACHE_SECONDS = 60  # an approval reaches the agent within a minute
COLUMNS = ("definition_id", "kind", "name", "tables", "definition", "status", "source", "external_id",
           "proposed_by", "updated_by", "created_at", "updated_at")
_approved = {"at": 0.0, "rows": []}


def _text(value, limit, what, required=False):
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if required and not text:
        raise SettingsError(f"{what} wajib diisi.")
    if len(text) > limit:
        raise SettingsError(f"{what} maksimal {limit} karakter.")
    return text


def _list(value, what, pattern=None):
    items = value if isinstance(value, list) else [v for v in (value or "").split(",")] if isinstance(value, str) else []
    items = [i.strip() for i in items if isinstance(i, str) and i.strip()]
    if len(items) > MAX_LIST or any(len(i) > 128 for i in items):
        raise SettingsError(f"{what}: maksimal {MAX_LIST} isian, masing-masing 128 karakter.")
    if pattern and any(not pattern.fullmatch(i) for i in items):
        raise SettingsError(f"{what} harus berupa nama kolom.")
    return list(dict.fromkeys(items))


def _tables(sql):
    """Every database.table the statement reads (CTE names excluded); unqualified tables are refused."""
    tree = sqlglot.parse_one(sql, read="clickhouse")
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    found = []
    for table in tree.find_all(exp.Table):
        if not table.db:
            if table.name in ctes:
                continue
            raise SettingsError(f"Tulis tabel lengkap dengan databasenya (database.tabel), bukan '{table.name}'.")
        found.append(f"{table.db}.{table.name}")
    return sorted(set(found))


def _checked_sql(sql, what):
    try:
        validate_sql(sql)
    except UnsafeSQL as error:
        raise SettingsError(f"{what} ditolak: {error}") from None
    return _tables(sql)


def metric_sql(name, definition, table):
    """The query a metric stands for; the agent adds WHERE / GROUP BY around it without changing the formula."""
    where = f" WHERE {definition['filter']}" if definition.get("filter") else ""
    return f"SELECT {definition['expression']} AS {name} FROM {table}{where}"


def clean(body):
    """(kind, name, tables, definition) from a form or tool call; raises SettingsError with a safe message."""
    if not isinstance(body, dict):
        raise SettingsError("Isian tidak valid.")
    kind = body.get("kind")
    if kind not in KINDS:
        raise SettingsError("Jenis harus metric atau verified_query.")
    name = body.get("name") if isinstance(body.get("name"), str) else ""
    if not NAME.fullmatch(name):
        raise SettingsError("Nama: huruf kecil, angka dan garis bawah, diawali huruf, maksimal 64 karakter.")
    common = {"description": _text(body.get("description"), MAX_TEXT, "Deskripsi"),
              "synonyms": _list(body.get("synonyms"), "Sinonim")}
    if kind == "verified_query":
        definition = {**common, "question": _text(body.get("question"), MAX_TEXT, "Pertanyaan", True),
                      "sql": (body.get("sql") or "").strip() if isinstance(body.get("sql"), str) else ""}
        if not definition["sql"] or len(definition["sql"]) > MAX_SQL:
            raise SettingsError(f"SQL wajib diisi, maksimal {MAX_SQL} karakter.")
        tables = _checked_sql(definition["sql"], "SQL")
        if not tables:
            raise SettingsError("SQL harus membaca setidaknya satu tabel.")
        return kind, name, tables, definition
    table = body.get("table") if isinstance(body.get("table"), str) else ""
    if not TABLE.fullmatch(table.strip()):
        raise SettingsError("Tabel ditulis sebagai database.tabel.")
    metric_type = body.get("metric_type") or "other"
    if metric_type not in METRIC_TYPES:
        raise SettingsError("Tipe metrik tidak dikenal.")
    definition = {**common, "expression": _text(body.get("expression"), MAX_EXPRESSION, "Rumus", True),
                  "metric_type": metric_type, "unit": _text(body.get("unit"), 40, "Satuan"),
                  "time_column": _text(body.get("time_column"), 128, "Kolom waktu"),
                  "dimensions": _list(body.get("dimensions"), "Dimensi", IDENTIFIER),
                  "filter": _text(body.get("filter"), MAX_TEXT, "Filter")}
    if definition["time_column"] and not IDENTIFIER.fullmatch(definition["time_column"]):
        raise SettingsError("Kolom waktu harus berupa nama kolom.")
    table = table.strip()
    # The formula must read exactly its own table: an expression smuggling "FROM other" is refused here.
    if _checked_sql(metric_sql(name, definition, table), "Rumus") != [table]:
        raise SettingsError("Rumus dan filter hanya boleh memakai kolom dari tabelnya sendiri.")
    return kind, name, [table], definition


def _row(values):
    row = dict(zip(COLUMNS, values))
    row["definition_id"] = str(row["definition_id"])
    for key in ("created_at", "updated_at"):
        row[key] = row[key].isoformat() if row[key] else None
    return row


def _select(env, where="", params=()):
    if not env.get("KAI_CATALOG_DSN"):
        return []
    with _connect(env) as connection:
        rows = connection.execute(
            f"SELECT {', '.join(COLUMNS)} FROM kai.semantic_definitions {where} ORDER BY kind, name", params).fetchall()
    return [_row(r) for r in rows]


def list_definitions(env=os.environ):
    return _select(env)


def _write(env, sql, params):
    if not env.get("KAI_CATALOG_DSN"):
        raise SettingsError("Penyimpanan semantic layer belum disiapkan (KAI_CATALOG_DSN).")
    try:
        with _connect(env) as connection:
            row = connection.execute(sql, params).fetchone()
    except Exception as error:
        if type(error).__name__ == "UniqueViolation":
            raise SettingsError("Nama ini sudah dipakai definisi lain dengan jenis yang sama.") from None
        raise
    _approved["at"] = 0.0
    if row is None:
        raise SettingsError("Definisi tidak ditemukan.")
    return _row(row)


def create(cleaned, status, source, user, env=os.environ):
    kind, name, tables, definition = cleaned
    return _write(env, f"INSERT INTO kai.semantic_definitions (kind, name, tables, definition, status, source, "
                       f"proposed_by, updated_by) VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s) RETURNING {', '.join(COLUMNS)}",
                  (kind, name, tables, json.dumps(definition), status, source, user, user))


def update(definition_id, cleaned, status, user, env=os.environ):
    kind, name, tables, definition = cleaned
    return _write(env, f"UPDATE kai.semantic_definitions SET kind=%s, name=%s, tables=%s, definition=%s::jsonb, status=%s, "
                       f"updated_by=%s, updated_at=now() WHERE definition_id=%s RETURNING {', '.join(COLUMNS)}",
                  (kind, name, tables, json.dumps(definition), status, user, definition_id))


def delete(definition_id, env=os.environ):
    return _write(env, f"DELETE FROM kai.semantic_definitions WHERE definition_id=%s RETURNING {', '.join(COLUMNS)}",
                  (definition_id,))


def save_draft(cleaned, user, env=os.environ):
    """Agent draft: new row, or a refresh of an existing draft. Approved/rejected rows are a reviewer's decision."""
    kind, name, tables, definition = cleaned
    if not env.get("KAI_CATALOG_DSN"):
        raise SettingsError("Penyimpanan semantic layer belum disiapkan. Hubungi administrator.")
    with _connect(env) as connection:
        existing = connection.execute("SELECT status FROM kai.semantic_definitions WHERE kind=%s AND name=%s",
                                      (kind, name)).fetchone()
        if existing and existing[0] != "draft":
            raise SettingsError(f"'{name}' sudah {'disetujui' if existing[0] == 'approved' else 'ditolak'} admin; "
                                "pakai nama lain atau minta admin mengubahnya di Admin Panel.")
        if not existing:
            drafts = connection.execute("SELECT count(*) FROM kai.semantic_definitions WHERE status='draft'").fetchone()[0]
            if drafts >= MAX_DRAFTS:
                raise SettingsError(f"Sudah ada {MAX_DRAFTS} draf menunggu tinjauan. Minta admin meninjaunya dulu.")
    if existing:
        row = _select(env, "WHERE kind=%s AND name=%s", (kind, name))[0]
        return update(row["definition_id"], cleaned, "draft", user, env)
    return create(cleaned, "draft", "ai", user, env)


async def approved():
    """Approved definitions, cached CACHE_SECONDS; [] when storage is not configured or unreachable."""
    if time.monotonic() - _approved["at"] < CACHE_SECONDS:
        return _approved["rows"]
    try:
        rows = await asyncio.to_thread(_select, os.environ, "WHERE status='approved'")
    except Exception:
        rows = _approved["rows"]  # keep the last good copy through a database blip
    _approved.update(at=time.monotonic(), rows=rows)
    return rows


def describe(row):
    """Text that search ranks a definition by."""
    d = row["definition"]
    head = d.get("question") or d.get("description") or ""
    return f"{row['name'].replace('_', ' ')}: {head} Sinonim: {', '.join(d.get('synonyms', []))}. {d.get('description', '')}"


def for_agent(row):
    d = row["definition"]
    if row["kind"] == "verified_query":
        return {"kind": "verified_query", "name": row["name"], "question": d["question"], "sql": d["sql"]}
    return {"kind": "metric", "name": row["name"], "description": d.get("description") or None,
            "sql": metric_sql(row["name"], d, row["tables"][0]), "unit": d.get("unit") or None,
            "time_column": d.get("time_column") or None, "dimensions": d.get("dimensions") or None}


async def propose_semantic_definition(
    kind: str, name: str, description: str = "", table: str = "", expression: str = "", metric_type: str = "other",
    unit: str = "", time_column: str = "", dimensions: list[str] | None = None, filter: str = "",
    synonyms: list[str] | None = None, question: str = "", sql: str = "",
) -> str:
    """Save a DRAFT semantic definition for admin review: a metric (kind=metric: table as database.table, expression
    as the aggregate formula e.g. sum(pendapatan_rp), metric_type, unit, time_column, dimensions, optional filter)
    or a verified query (kind=verified_query: question and one read-only SQL with database.table names).

    Checked against your own ClickHouse view (tables, columns, EXPLAIN); nothing is executed. Drafts are not used
    until an admin approves them in the Admin Panel. Never put example values or personal data in synonyms/text.
    """
    try:
        cleaned = clean({"kind": kind, "name": name, "description": description, "table": table,
                         "expression": expression, "metric_type": metric_type, "unit": unit,
                         "time_column": time_column, "dimensions": dimensions or [], "filter": filter,
                         "synonyms": synonyms or [], "question": question, "sql": sql})
    except SettingsError as error:
        raise GatewayError("INVALID_ARGUMENT", str(error)) from None
    _, _, tables, definition = cleaned
    for ref in tables:
        database, table_name = TABLE.fullmatch(ref).groups()
        schema = json.loads(await get_table_schema(database, table_name))  # ACCESS_DENIED when not visible
        if kind == "metric":
            columns = {c["name"] for c in schema["columns"]}
            missing = [c for c in [definition["time_column"], *definition["dimensions"]] if c and c not in columns]
            if missing:
                raise GatewayError("INVALID_ARGUMENT", f"Kolom tidak ada di {ref}: {', '.join(missing)}.")
    query = metric_sql(name, definition, tables[0]) if kind == "metric" else definition["sql"]
    try:
        await run_query_async(f"EXPLAIN {query}")  # resolves tables and columns as the caller, reads no rows
    except Exception:
        raise GatewayError("INVALID_ARGUMENT", "ClickHouse menolak SQL ini (kolom, fungsi atau hak akses). "
                           "Periksa dengan get_table_schema lalu perbaiki.") from None
    user = (get_http_headers() or {}).get("x-librechat-user-id", "").strip()
    try:
        await asyncio.to_thread(save_draft, cleaned, user if USER.fullmatch(user) else None)
    except SettingsError as error:
        raise GatewayError("INVALID_ARGUMENT", str(error)) from None
    except Exception:
        raise GatewayError("DEPENDENCY_UNAVAILABLE", "Draf tidak bisa disimpan sekarang. Coba lagi nanti.") from None
    return json.dumps({"saved": "draft", "kind": kind, "name": name,
                       "message": "Draf tersimpan. Admin perlu menyetujuinya di Admin Panel → Integrasi → "
                                  "Semantic layer sebelum dipakai."}, ensure_ascii=False)


def _status(body):
    status = body.get("status", "draft")
    if status not in STATUSES:
        raise SettingsError("Status tidak dikenal.")
    return status


def _user(body):
    user = body.get("updated_by")
    if not isinstance(user, str) or not USER.fullmatch(user):
        raise SettingsError("Isian tidak valid.")
    return user


async def definitions_request(request):
    if not _authorized(request):
        return JSONResponse({"message": "Unauthorized."}, status_code=401)
    definition_id = (getattr(request, "path_params", None) or {}).get("definition_id")
    try:
        if request.method == "GET":
            rows = await asyncio.to_thread(list_definitions)
            return JSONResponse({"definitions": rows, "storage_available": bool(os.environ.get("KAI_CATALOG_DSN"))},
                                headers={"Cache-Control": "no-store"})
        body = await _body(request)
        if not isinstance(body, dict):
            raise SettingsError("Isian tidak valid.")
        user = _user(body)
        if request.method != "POST" and not UUID.fullmatch(definition_id or ""):
            raise SettingsError("Definisi tidak ditemukan.")
        if request.method == "DELETE":
            row = await asyncio.to_thread(delete, definition_id)
        elif request.method == "POST":
            row = await asyncio.to_thread(create, clean(body), _status(body), "manual", user)
        else:
            row = await asyncio.to_thread(update, definition_id, clean(body), _status(body), user)
        return JSONResponse(row, headers={"Cache-Control": "no-store"})
    except SettingsError as error:
        return JSONResponse({"message": str(error)}, status_code=400)
    except Exception:
        return JSONResponse({"message": "Semantic layer tidak bisa dibaca atau disimpan."}, status_code=503)


def register_semantic_routes(mcp):
    mcp.custom_route("/v1/internal/semantic", methods=["GET", "POST"])(definitions_request)
    mcp.custom_route("/v1/internal/semantic/{definition_id}", methods=["PUT", "DELETE"])(definitions_request)
