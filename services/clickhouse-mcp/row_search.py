"""search_rows_by_meaning (AF-03): rank the rows of one read-only SELECT by meaning, not keywords.

The SELECT runs through the same gateway path as execute_readonly_sql (SQL policy, readonly=1, the
caller's own ClickHouse login, output limits, audit), so grants and row policies decide which rows exist.
Ranking uses the local bge-m3 server (no commercial model); when it is down, word overlap is used and the
score column says so. Candidates are capped so one call stays inside the MCP timeout on CPU.
ponytail: rows are embedded per call (vectors cached in process memory); a stored index (pgvector) is
the upgrade if users search the same large text columns repeatedly.
"""

import json
from typing import Annotated

import httpx2 as httpx
from mcp_clickhouse.mcp_server import run_query_async
from pydantic import Field

import metadata_search as metadata
from tool_errors import GatewayError

MAX_CANDIDATES = 200
MAX_TEXT_CHARS = 1000


def _text(row):
    """The row's string cells, which is what a meaning search can compare."""
    return " | ".join(value for value in row if isinstance(value, str)).strip()[:MAX_TEXT_CHARS]


async def search_rows_by_meaning(
    sql: str,
    query: Annotated[str, Field(min_length=1, max_length=1000)],
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> str:
    """Find rows whose text means something similar to a question (e.g. complaints like "late delivery").

    sql: one read-only SELECT returning an id column plus the text column(s) to compare, filtered and
    limited to at most 200 rows (add WHERE/LIMIT). Returns the best matching rows with a score column.
    """
    if not query.strip():
        raise GatewayError("INVALID_ARGUMENT", "Describe what the rows should be about.")
    payload = json.loads(await run_query_async(sql))
    columns, rows = payload["columns"], payload["rows"]
    if len(rows) > MAX_CANDIDATES:
        raise GatewayError(
            "INVALID_ARGUMENT",
            f"The SELECT returned more than {MAX_CANDIDATES} rows; narrow it with WHERE or LIMIT {MAX_CANDIDATES}.",
        )
    texts = [_text(row) for row in rows]
    if rows and not any(texts):
        raise GatewayError("INVALID_ARGUMENT", "Select at least one text column to compare.")
    column = "score_semantic"
    try:
        if not metadata.EMBEDDINGS_URL:
            raise OSError("embeddings not configured")
        vectors = await metadata._embed([query, *[t or " " for t in texts]])
        scores = [metadata._cosine(vectors[0], v) for v in vectors[1:]]
    except (OSError, httpx.HTTPError, KeyError, ValueError):
        column = "score_keyword"  # local embedding server down or not configured
        scores = metadata._lexical(query, texts)
    ranked = sorted(zip(scores, rows), key=lambda pair: pair[0], reverse=True)[:limit]
    return json.dumps(
        {"columns": [*columns, column], "rows": [[*row, round(score, 3)] for score, row in ranked]},
        ensure_ascii=False,
    )
