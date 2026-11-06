"""Project SQL tool contract using the official request-scoped connector."""

from typing import Annotated

from pydantic import Field
from mcp_clickhouse.mcp_server import run_query_async

from tool_errors import GatewayError


async def execute_readonly_sql(
    sql: str, purpose: Annotated[str, Field(min_length=1, max_length=500)]
) -> str:
    """Run one read-only SQL statement as your own ClickHouse user. Explain its purpose briefly.

    Returns bounded rows with a truncation flag and an audit request_id. Identity is supplied
    by your saved connection, never by tool arguments. Database resource policies still apply.
    """
    if not purpose.strip():
        raise GatewayError(
            "INVALID_ARGUMENT", "Provide a nonblank purpose for the query."
        )
    return await run_query_async(sql)
