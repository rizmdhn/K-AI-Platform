"""search_database_metadata (P4-05/P4-06): find tables relevant to a question among those the caller can see.

Candidates come from the caller's own ClickHouse session (the per-request override), so ClickHouse grants
decide visibility and a table the user cannot access is never returned (SEC-12). Ranking uses the local
bge-m3 embedding server (no commercial model); when it is unavailable, ranking falls back to word overlap.
ponytail: embeddings are cached in process memory and recomputed after restart; persist them in pgvector
(P4-05 "db_metadata namespace") when catalogs grow past a few thousand tables.
"""

import json
import math
import os
import re
from array import array

import httpx2 as httpx
from mcp_clickhouse.mcp_server import list_databases_async, list_tables_async

from openmetadata import approved_metadata
from tool_errors import GatewayError

EMBEDDINGS_URL = os.getenv("KAI_EMBEDDINGS_URL", "")
EMBEDDINGS_MODEL = "bge-m3"
MAX_TABLES = 500
MAX_COLUMNS_SHOWN = 40
HIDDEN_DATABASES = {"system", "information_schema"}
MAX_DESCRIPTION_CHARS = 4000  # keeps one very wide table under the embedding server's 8192-token window
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


async def _embed(texts):
    missing = [t for t in texts if t not in _cache]
    if missing:
        if len(_cache) > 20000:
            _cache.clear()
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
            for start in range(0, len(missing), 32):
                batch = missing[start : start + 32]
                response = await client.post(EMBEDDINGS_URL, json={"model": EMBEDDINGS_MODEL, "input": batch})
                response.raise_for_status()
                for item in response.json()["data"]:
                    _cache[batch[item["index"]]] = array("f", item["embedding"])
    return [_cache[t] for t in texts]


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a) * sum(y * y for y in b)) or 1
    )


async def _visible_tables():
    tables = []
    for database in json.loads(await list_databases_async()):
        if database.lower() in HIDDEN_DATABASES:
            continue
        token = None
        while len(tables) < MAX_TABLES:
            page = json.loads(
                await list_tables_async(
                    database=database, page_token=token, page_size=100, include_detailed_columns=True
                )
            )
            tables += page["tables"]
            token = page.get("next_page_token")
            if not token:
                break
    return tables[:MAX_TABLES]


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
    method = "semantic"
    try:
        if not EMBEDDINGS_URL:
            raise OSError("embeddings not configured")
        vectors = await _embed([query, *docs])
        scores = [_cosine(vectors[0], v) for v in vectors[1:]]
    except (OSError, httpx.HTTPError, KeyError, ValueError):
        method = "keyword"  # local embedding server down or not configured
        scores = _lexical(query, docs)
    ranked = sorted(zip(scores, tables, views), key=lambda item: item[0], reverse=True)[:limit]
    results = [
        {
            "database": t["database"],
            "table": t["name"],
            "comment": t.get("comment") or None,
            **_approved_fields(view),
            "columns": [c["name"] for c in t.get("columns", [])][:MAX_COLUMNS_SHOWN],
            "score": round(score, 3),
        }
        for score, t, view in ranked
    ]
    return json.dumps({"query": query, "method": method, "results": results}, ensure_ascii=False)
