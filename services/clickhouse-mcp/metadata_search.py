"""search_database_metadata (P4-05/P4-06): find tables relevant to a question among those the caller can see.

Candidates come from the caller's own ClickHouse session (the per-request override), so ClickHouse grants
decide visibility and a table the user cannot access is never returned (SEC-12). Ranking uses the local
bge-m3 embedding server (no commercial model); when it is unavailable, ranking falls back to word overlap.
Table embeddings are built in the background (CPU bge-m3 needs ~1 s per table, minutes for a staging-sized
catalog); until a caller's catalog is fully embedded, that search ranks by word overlap so it never blocks.
ponytail: embeddings are cached in process memory and rebuilt in the background after restart; persist them in
pgvector (P4-05 "db_metadata namespace") when catalogs grow past a few thousand tables.
"""

import asyncio
import json
import math
import os
import re
from array import array

import httpx2 as httpx
import time

from mcp_clickhouse import clients
from mcp_clickhouse.mcp_server import run_query_async

from openmetadata import approved_metadata
from tool_errors import GatewayError

EMBEDDINGS_URL = os.getenv("KAI_EMBEDDINGS_URL", "")
EMBEDDINGS_MODEL = "bge-m3"
MAX_TABLES = 5000
MAX_COLUMNS_SHOWN = 15
HIDDEN_DATABASES = {"system", "information_schema"}
CATALOG_TTL = 600  # seconds; a new table shows up in search within 10 minutes
_catalogs = {}  # ClickHouse user -> (expires_at, tables); keyed per user because grants decide visibility
_HIDDEN_SQL = "database NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema')"
TABLES_SQL = (
    f"SELECT database, name, comment, total_rows, sorting_key FROM system.tables WHERE {_HIDDEN_SQL} "
    f"AND NOT is_temporary AND name NOT LIKE '.%' ORDER BY database, name LIMIT {MAX_TABLES}"
)
COLUMNS_SQL = (
    f"SELECT database, table, name, type, comment FROM system.columns WHERE {_HIDDEN_SQL} "
    "ORDER BY database, table, position LIMIT 100000"
)
LAYER_WEIGHT = {"gold": 1.0, "silver": 0.9, "bronze": 0.6, "raw": 0.6}
NOISE = re.compile(r"(_coba|_dulu|_tmp|_temp|_test|_try|_snap|_bak|_backup|_old|_copy|_\d{2,})(_|$)", re.I)
MAX_DESCRIPTION_CHARS = 1500  # embedding time grows with length; name, comment and the first columns carry the meaning
EMBED_BATCH = 8  # a query embedding queued behind background work waits for at most one small batch
QUERY_EMBED_TIMEOUT = 15
_cache = {}  # text -> float32 vector (array, ~4 KB for bge-m3 instead of ~32 KB as a list)


def _visible_view(table, approved):
    """OpenMetadata view limited to the columns this caller's grants return (column-level grants)."""
    if not approved:
        return None
    visible = {c["name"] for c in table.get("columns", [])}
    return {**approved, "columns": {k: v for k, v in approved.get("columns", {}).items() if k in visible}}


def _describe(table, approved=None):
    approved = approved or {}
    meanings = approved.get("columns", {})
    columns = ", ".join(
        f"{c['name']} ({c.get('column_type', '')}){': ' + c['comment'] if c.get('comment') else ''}"
        + (f" [{meanings[c['name']]}]" if c["name"] in meanings else "")
        for c in table.get("columns", [])
    )
    comment = f": {table['comment']}" if table.get("comment") else ""
    extra = f" {approved['description']}." if approved.get("description") else ""
    if approved.get("tags"):
        extra += " Tags: " + ", ".join(approved["tags"]) + "."
    return f"{table['database']}.{table['name']}{comment}.{extra} Columns: {columns}"[:MAX_DESCRIPTION_CHARS]


def _words(text):
    return set(re.findall(r"[a-z0-9]+", text.lower().replace("_", " ")))


def _lexical(query, docs):
    q = _words(query)
    return [len(q & _words(d)) / (len(q) or 1) for d in docs]


async def _embed(texts, timeout):
    missing = [t for t in dict.fromkeys(texts) if t not in _cache]
    if missing:
        if len(_cache) > 20000:
            _cache.clear()
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            for start in range(0, len(missing), EMBED_BATCH):
                batch = missing[start : start + EMBED_BATCH]
                response = await client.post(EMBEDDINGS_URL, json={"model": EMBEDDINGS_MODEL, "input": batch})
                response.raise_for_status()
                for item in response.json()["data"]:
                    _cache[batch[item["index"]]] = array("f", item["embedding"])
    return [_cache[t] for t in texts]


_warming = None  # the one background task embedding table descriptions


async def _embed_quietly(docs):
    try:
        await _embed(docs, timeout=300)
    except (OSError, httpx.HTTPError, KeyError, ValueError):
        pass  # the next search starts it again


def _warm_later(docs):
    global _warming
    if _warming is None or _warming.done():
        _warming = asyncio.create_task(_embed_quietly(docs))


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a) * sum(y * y for y in b)) or 1
    )


def _rows(text):
    payload = json.loads(text)
    return [dict(zip(payload["columns"], row)) for row in payload["rows"]]


async def _load_catalog():
    """Two metadata queries (system.tables + system.columns) instead of paging list_tables per database.

    They run as the caller (request overrides), so ClickHouse grants filter both lists.
    """
    tables = {
        (t["database"], t["name"]): {**t, "columns": []}
        for t in _rows(await run_query_async(TABLES_SQL))
        if t["database"].lower() not in HIDDEN_DATABASES
    }
    for c in _rows(await run_query_async(COLUMNS_SQL)):
        table = tables.get((c["database"], c["table"]))
        if table is not None:
            table["columns"].append({"name": c["name"], "column_type": c["type"], "comment": c["comment"]})
    return list(tables.values())


async def _visible_tables():
    overrides = clients._get_client_config_overrides() or {}
    user = overrides.get("username")
    if not user:  # no per-user identity to key on: never share a cached catalog
        return await _load_catalog()
    now = time.monotonic()
    cached = _catalogs.get(user)
    if cached and cached[0] > now:
        return cached[1]
    tables = await _load_catalog()
    if len(_catalogs) > 1000:
        _catalogs.clear()
    _catalogs[user] = (now + CATALOG_TTL, tables)
    return tables


def _weight(table):
    """Prefer curated layers and push scratch copies (_coba, _tmp, ...) and empty tables down."""
    weight = LAYER_WEIGHT.get(table["database"].lower(), 0.8)
    if NOISE.search(table["name"]):
        weight *= 0.5
    if table.get("total_rows") == 0:
        weight *= 0.5
    return weight


def _approved_fields(approved):
    if not approved:
        return {}
    fields = {k: approved[k] for k in ("description", "tags") if approved.get(k)}
    if approved.get("columns"):
        fields["column_meanings"] = dict(list(approved["columns"].items())[:MAX_COLUMNS_SHOWN])
    if fields:
        fields["description_source"] = "OpenMetadata, curated by the data team"
    return fields


async def search_database_metadata(query: str, limit: int = 5) -> str:
    """Find the tables most relevant to a question among those your ClickHouse account can see.

    Use this before writing SQL when you do not know which table holds the data. Returns table names,
    comments and columns; follow up with get_table_schema for exact types.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise GatewayError("INVALID_ARGUMENT", "Provide a short question or keywords to search for.")
    if not isinstance(limit, int) or not 1 <= limit <= 20:
        raise GatewayError("INVALID_ARGUMENT", "limit must be between 1 and 20.")
    tables = await _visible_tables()
    # Curated OpenMetadata metadata (FR-11), only for tables and columns this caller's grants already return.
    catalog = await approved_metadata()
    views = [_visible_view(t, catalog.get((t["database"], t["name"]))) for t in tables]
    docs = [_describe(t, v) for t, v in zip(tables, views)]
    method, scores = "keyword", None
    if EMBEDDINGS_URL and all(d in _cache for d in docs):
        try:
            question = (await _embed([query], QUERY_EMBED_TIMEOUT))[0]
            scores, method = [_cosine(question, _cache[d]) for d in docs], "semantic"
        except (OSError, httpx.HTTPError, KeyError, ValueError):
            scores = None  # local embedding server down or slow
    elif EMBEDDINGS_URL:
        _warm_later(docs)  # tables not embedded yet: rank by words now, semantically once the task finishes
    if scores is None:
        scores = _lexical(query, docs)
    scores = [score * _weight(t) for score, t in zip(scores, tables)]
    ranked = sorted(zip(scores, tables, views), key=lambda item: item[0], reverse=True)[:limit]
    results = [
        {
            "database": t["database"],
            "table": t["name"],
            "comment": t.get("comment") or None,
            "sorting_key": t.get("sorting_key") or None,
            **_approved_fields(view),
            "columns": [c["name"] for c in t.get("columns", [])][:MAX_COLUMNS_SHOWN],
            "score": round(score, 3),
        }
        for score, t, view in ranked
    ]
    return json.dumps({"query": query, "method": method, "results": results}, ensure_ascii=False)
