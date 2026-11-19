"""Semantic layer: approved metric definitions and verified queries the CH agent reuses instead of inventing SQL.

Rows live in kai.semantic_definitions (kai_catalog role, KAI_CATALOG_DSN). Drafts come from the local-model job
(semantic_drafts.py, validated against the requesting admin's own ClickHouse view) or the Admin Panel form; only
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
from sqlglot import exp
from starlette.responses import JSONResponse

from integration_settings import SettingsError, _authorized, _body, _connect
from sql_policy import UnsafeSQL
from sql_policy import validate as validate_sql

KINDS = ("metric", "verified_query")
STATUSES = ("draft", "approved", "rejected")
METRIC_TYPES = ("sum", "count", "count_distinct", "average", "min", "max", "ratio", "other")
NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
TABLE = re.compile(r"([A-Za-z0-9_-]{1,128})\.([A-Za-z0-9_-]{1,128})")
USER = re.compile(r"[0-9a-f]{24}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MAX_TEXT, MAX_EXPRESSION, MAX_SQL, MAX_LIST = 500, 1000, 4000, 30
MAX_DRAFTS = 20000  # caps AI drafts waiting for review (~500 tables x a few drafts each, many times over)
PAGE_SIZES = (10, 25, 50, 100)
SORTS = {"name": "name", "updated_at": "updated_at", "kind": "kind", "status": "status", "table": "tables[1]"}
MAX_BULK = 1000  # ids per bulk request; "everything matching the filter" goes through the filter form instead
SOURCES = ("ai", "manual", "catalog")
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


def _filters(query):
    """Validated list filters -> (WHERE sql, params). Unknown or malformed values are rejected, never ignored."""
    clauses, params = [], []
    for key, allowed in (("status", STATUSES), ("kind", KINDS), ("source", SOURCES)):
        value = query.get(key) or ""
        if value:
            if value not in allowed:
                raise SettingsError("Filter tidak valid.")
            clauses.append(f"{key} = %s")
            params.append(value)
    database, table, words = query.get("database") or "", query.get("table") or "", (query.get("q") or "").strip()
    if database:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", database):
            raise SettingsError("Filter tidak valid.")
        clauses.append("EXISTS (SELECT 1 FROM unnest(tables) t WHERE split_part(t, '.', 1) = %s)")
        params.append(database)
    if table:
        if not TABLE.fullmatch(table):
            raise SettingsError("Filter tidak valid.")
        clauses.append("tables @> ARRAY[%s]::text[]")
        params.append(table)
    if words:
        if len(words) > 100:
            raise SettingsError("Kata pencarian terlalu panjang.")
        like = "%" + words.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(name ILIKE %s OR array_to_string(tables, ' ') ILIKE %s OR definition::text ILIKE %s)")
        params += [like, like, like]
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def _number(value, default):
    if value in (None, ""):
        return default
    if not isinstance(value, str) or not value.isdigit() or len(value) > 6:
        raise SettingsError("Pengaturan tabel tidak valid.")
    return int(value)


def page_definitions(query, env=os.environ):
    """One page of definitions for the Admin Panel table, plus totals per status for the whole (unfiltered) layer."""
    where, params = _filters(query)
    size, page = _number(query.get("size"), 25), _number(query.get("page"), 1)
    sort, direction = query.get("sort") or "updated_at", (query.get("dir") or "desc").lower()
    if size not in PAGE_SIZES or not 1 <= page <= 100000 or sort not in SORTS or direction not in ("asc", "desc"):
        raise SettingsError("Pengaturan tabel tidak valid.")
    if not env.get("KAI_CATALOG_DSN"):
        return {"definitions": [], "total": 0, "counts": {}, "storage_available": False}
    with _connect(env) as connection:
        total = connection.execute(f"SELECT count(*) FROM kai.semantic_definitions {where}", params).fetchone()[0]
        rows = connection.execute(
            f"SELECT {', '.join(COLUMNS)} FROM kai.semantic_definitions {where} "
            f"ORDER BY {SORTS[sort]} {direction}, definition_id LIMIT %s OFFSET %s",
            (*params, size, (page - 1) * size)).fetchall()
        counts = dict(connection.execute("SELECT status, count(*) FROM kai.semantic_definitions GROUP BY status").fetchall())
    return {"definitions": [_row(r) for r in rows], "total": total, "counts": counts, "storage_available": True}


def covered_tables(env=os.environ, database=None):
    """table ref -> {"metric": n, "verified_query": n, "draft": n, "approved": n, "rejected": n} (one aggregate)."""
    if not env.get("KAI_CATALOG_DSN"):
        return {}
    sql = ("SELECT t, kind, status, count(*) FROM kai.semantic_definitions, unnest(tables) t "
           + ("WHERE split_part(t, '.', 1) = %s " if database else "") + "GROUP BY 1, 2, 3")
    result = {}
    with _connect(env) as connection:
        for ref, kind, status, n in connection.execute(sql, (database,) if database else ()).fetchall():
            item = result.setdefault(ref, {"metric": 0, "verified_query": 0, "draft": 0, "approved": 0, "rejected": 0})
            item[kind] += n
            item[status] += n
    return result


def bulk_status(body, env=os.environ):
    """Approve/reject many in one UPDATE: either explicit ids, or every definition matching a list filter."""
    status, user = _status(body), _user(body)
    if not env.get("KAI_CATALOG_DSN"):
        raise SettingsError("Penyimpanan semantic layer belum disiapkan (KAI_CATALOG_DSN).")
    ids, query = body.get("ids"), body.get("filter")
    if ids is not None:
        if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_BULK
                or not all(isinstance(i, str) and UUID.fullmatch(i) for i in ids)):
            raise SettingsError("Pilihan tidak valid.")
        where, params = "WHERE definition_id = ANY(%s::uuid[])", [ids]
    elif isinstance(query, dict) and all(isinstance(v, str) for v in query.values()):
        where, params = _filters(query)
        if not where:
            raise SettingsError("Pilih filter dulu; mengubah seluruh semantic layer sekaligus tidak diizinkan.")
    else:
        raise SettingsError("Pilihan tidak valid.")
    with _connect(env) as connection:
        changed = connection.execute(f"UPDATE kai.semantic_definitions SET status=%s, updated_by=%s, updated_at=now() "
                                     f"{where} AND status <> %s", (status, user, *params, status)).rowcount
    _approved["at"] = 0.0
    return {"changed": changed}


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
    """AI draft: new row, or a refresh of an existing draft. Approved/rejected rows are a reviewer's decision."""
    kind, name, tables, definition = cleaned
    if not env.get("KAI_CATALOG_DSN"):
        raise SettingsError("Penyimpanan semantic layer belum disiapkan. Hubungi administrator.")
    with _connect(env) as connection:
        existing = connection.execute("SELECT status FROM kai.semantic_definitions WHERE kind=%s AND name=%s",
                                      (kind, name)).fetchone()
        if existing and existing[0] != "draft":
            raise SettingsError(f"nama sudah {'disetujui' if existing[0] == 'approved' else 'ditolak'} sebelumnya")
        if not existing:
            drafts = connection.execute("SELECT count(*) FROM kai.semantic_definitions WHERE status='draft'").fetchone()[0]
            if drafts >= MAX_DRAFTS:
                raise SettingsError(f"sudah ada {MAX_DRAFTS} draf menunggu tinjauan")
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
            result = await asyncio.to_thread(page_definitions, dict(request.query_params))
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
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


async def bulk_request(request):
    if not _authorized(request):
        return JSONResponse({"message": "Unauthorized."}, status_code=401)
    try:
        body = await _body(request)
        if not isinstance(body, dict):
            raise SettingsError("Isian tidak valid.")
        return JSONResponse(await asyncio.to_thread(bulk_status, body), headers={"Cache-Control": "no-store"})
    except SettingsError as error:
        return JSONResponse({"message": str(error)}, status_code=400)
    except Exception:
        return JSONResponse({"message": "Semantic layer tidak bisa disimpan."}, status_code=503)


def register_semantic_routes(mcp):
    mcp.custom_route("/v1/internal/semantic/bulk", methods=["POST"])(bulk_request)
    mcp.custom_route("/v1/internal/semantic", methods=["GET", "POST"])(definitions_request)
    mcp.custom_route("/v1/internal/semantic/{definition_id}", methods=["PUT", "DELETE"])(definitions_request)
