"""Exact table schema lookup through the official per-request metadata connector."""

import json

from mcp_clickhouse.mcp_server import list_tables_async

from result_limiter import MAX_RESULT_BYTES
from tool_errors import GatewayError


async def get_table_schema(database: str, table: str) -> str:
    """Get column names, types and comments for a table visible to your ClickHouse account."""
    if any(
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 256
        or "\x00" in name
        for name in (database, table)
    ) or database.lower() in {"system", "information_schema"}:
        raise GatewayError(
            "INVALID_ARGUMENT", "Provide a valid application database and table name."
        )
    # Escape LIKE wildcards so an exact lookup cannot enumerate neighboring tables.
    pattern = table.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    response = json.loads(
        await list_tables_async(
            database=database, like=pattern, page_size=1, include_detailed_columns=True
        )
    )
    matches = [
        item
        for item in response["tables"]
        if item["database"] == database and item["name"] == table
    ]
    if not matches:
        raise GatewayError("ACCESS_DENIED", "Table not found or not accessible.")
    item = matches[0]
    payload = {
        "database": database,
        "table": table,
        "engine": item["engine"],
        "columns": [
            {
                "name": column["name"],
                "type": column["column_type"],
                "comment": column.get("comment"),
            }
            for column in item["columns"]
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_RESULT_BYTES:
        raise GatewayError(
            "QUERY_LIMIT", "QUERY_LIMIT: Schema exceeds the output byte limit."
        )
    return text
