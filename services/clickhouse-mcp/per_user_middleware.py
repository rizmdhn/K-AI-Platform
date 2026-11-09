"""mcp-clickhouse middleware: every tool call runs as the caller's own ClickHouse user (P3-10)
and leaves one audit line (P3-07).

Managed mode resolves the trusted LibreChat ID through approved bindings. Explicit manual
development mode accepts personal CH headers. Neither falls back to the base CLICKHOUSE_USER.
Load with MCP_MIDDLEWARE_MODULE=per_user_middleware (this folder on PYTHONPATH).

Audit lines go to the "kai.audit" logger as JSON. They carry a SQL digest, never the SQL text,
password or result rows. The same request_id is sent to ClickHouse as log_comment, so the full
query can be found in system.query_log by an authorized operator.
"""

import hashlib
import asyncio
import json
import logging
import logging.handlers
import os
import time
import uuid

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_context, get_http_headers
from fastmcp.server.middleware import Middleware
from fastmcp.tools import Tool
from fastmcp.tools.base import ToolResult
import clickhouse_connect.common
from mcp_clickhouse import clients as ch_clients
from mcp_clickhouse.mcp_server import CLIENT_CONFIG_OVERRIDES_KEY

from binding_credentials import get_managed_credentials
from warehouse_enrollment import register_warehouse_routes
from warehouse_readiness import register_readiness_routes
from health_routes import register_health_routes
from problems import register_problem_routes
import gateway_metrics
from result_limiter import ResultLimitError, limit_result
from schema_tool import get_table_schema
from metadata_search import search_database_metadata
from row_search import search_rows_by_meaning
from comment_sql import suggest_comment_sql
from batch_jobs import get_row_labeling_job, register_job_routes, start_row_labeling
from sql_tool import execute_readonly_sql
from sql_policy import UnsafeSQL, validate
from tool_errors import ERROR_OUTCOMES, GatewayError, error_payload

audit = logging.getLogger("kai.audit")


def _retention_days():
    raw = os.getenv("KAI_LOG_RETENTION_DAYS") or "90"
    if not raw.isdigit() or not 1 <= int(raw) <= 3650:
        raise RuntimeError("KAI_LOG_RETENTION_DAYS must be a whole number of days between 1 and 3650.")
    return int(raw)


def attach_audit_file(directory):
    """P6-11: also keep audit lines on disk, one file per UTC day, for KAI_LOG_RETENTION_DAYS days."""
    handler = logging.handlers.TimedRotatingFileHandler(
        os.path.join(directory, "audit.jsonl"), when="midnight", utc=True, backupCount=_retention_days()
    )
    formatter = logging.Formatter('{"ts":"%(asctime)s","audit":%(message)s}', "%Y-%m-%dT%H:%M:%SZ")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    audit.addHandler(handler)
    return handler


if os.getenv("KAI_AUDIT_LOG_DIR"):
    attach_audit_file(os.environ["KAI_AUDIT_LOG_DIR"])
SQL_FIELDS = {"run_query": "query", "execute_readonly_sql": "sql", "search_rows_by_meaning": "sql"}
# Tools that also carry SQL (policy check + audit hash) but return no query rows, so no result limiting.
CHECKED_SQL_FIELDS = {**SQL_FIELDS, "start_row_labeling": "sql"}
# Server-side ceilings sent with every query (P3-03). The DBA settings profile stays authoritative:
# any of these it locks (CONST/readonly) is dropped below and the profile value applies.
QUERY_LIMITS = {
    "max_execution_time": int(os.getenv("KAI_CH_MAX_EXECUTION_TIME") or "25"),  # below the MCP 30 s timeout
    "max_memory_usage": int(os.getenv("KAI_CH_MAX_MEMORY_USAGE") or str(4 * 1024**3)),
    "max_rows_to_read": int(os.getenv("KAI_CH_MAX_ROWS_TO_READ") or str(10**9)),
    "max_bytes_to_read": int(os.getenv("KAI_CH_MAX_BYTES_TO_READ") or str(100 * 1024**3)),
}
# A DBA profile may lock settings (e.g. Docs/user_matrix.md: readonly=2 and CONST limits). Sending
# a locked setting fails every query, so let the profile win: drop settings locked for this user.
clickhouse_connect.common.set_setting("invalid_setting_action", "drop")
_upstream_query_settings = ch_clients.build_query_settings


def _readonly_query_settings(client):
    # Dropping must not drop read-only itself: refuse an account whose readonly is locked at 0, or whose
    # readonly cannot be read at all (clickhouse-connect would then drop readonly=1 as unknown).
    setting = (getattr(client, "server_settings", None) or {}).get("readonly")
    if setting is None or (setting.readonly and str(setting.value) == "0"):
        raise GatewayError(
            "ACCESS_DENIED",
            "Your ClickHouse account cannot be limited to read-only access, so it is not allowed here. "
            "Ask your administrator for a read-only account.",
        )
    return _upstream_query_settings(client)


# queries.py looks this up on the module per query, so the guard covers run_query and execute_readonly_sql.
ch_clients.build_query_settings = _readonly_query_settings


def _header(headers, name):
    value = headers.get(name, "").strip()
    # An unresolved LibreChat placeholder means the value was never supplied.
    return "" if "{{" in value else value


def _row_count(result):
    try:
        return len(json.loads(result.content[0].text)["rows"])
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None


class PerUserClickHouse(Middleware):
    async def on_call_tool(self, context, call_next):
        started = time.monotonic()
        headers = get_http_headers()
        tool = context.message.name
        args = context.message.arguments or {}
        managed = os.getenv("KAI_BINDING_MODE", "managed") == "managed"
        user, password = (
            ("", "")
            if managed
            else (_header(headers, "x-ch-user"), _header(headers, "x-ch-password"))
        )
        query = args.get(CHECKED_SQL_FIELDS[tool]) if tool in CHECKED_SQL_FIELDS else None
        record = {
            "request_id": uuid.uuid4().hex,
            "tool": tool,
            "ch_user": user or None,
            "librechat_user": _header(headers, "x-librechat-user-id") or None,
            "conversation": _header(headers, "x-librechat-conversation-id") or None,
            "database": args.get("database"),
            "sql_sha256": (
                hashlib.sha256(query.encode()).hexdigest()
                if isinstance(query, str)
                else None
            ),
            "outcome": "error",
        }
        if tool == "execute_readonly_sql":
            purpose = args.get("purpose")
            record["purpose_sha256"] = (
                hashlib.sha256(purpose.encode()).hexdigest()
                if isinstance(purpose, str)
                else None
            )
        try:
            if not managed and (not user or not password):
                record["outcome"] = "no_credentials"
                raise GatewayError(
                    "AUTH_REQUIRED",
                    "Add your ClickHouse username and password in this tool's settings first.",
                )
            if tool == "execute_readonly_sql" and set(args) != {"sql", "purpose"}:
                raise GatewayError(
                    "INVALID_ARGUMENT",
                    "Exactly sql and purpose are required; identity and other fields are not accepted.",
                )
            if tool in CHECKED_SQL_FIELDS:
                try:
                    validate(query)
                except UnsafeSQL as error:
                    record["outcome"] = "unsafe_sql"
                    raise GatewayError("UNSAFE_SQL", str(error)) from None

            if managed:
                user, password = await get_managed_credentials(record["librechat_user"])
                record["ch_user"] = user
            await get_context().set_state(
                CLIENT_CONFIG_OVERRIDES_KEY,
                {
                    "username": user,
                    "password": password,
                    "settings": {**QUERY_LIMITS, "readonly": 1, "log_comment": record["request_id"]},
                },
                serializable=False,
            )
            result = await call_next(context)
            if getattr(result, "is_error", False):
                raise ToolError(
                    " ".join(getattr(block, "text", "") for block in result.content)
                )
            if tool in SQL_FIELDS:
                try:
                    if len(result.content) != 1 or not isinstance(
                        getattr(result.content[0], "text", None), str
                    ):
                        raise ResultLimitError(
                            "QUERY_LIMIT: Invalid query result format."
                        )
                    text = limit_result(
                        result.content[0].text,
                        request_id=(
                            record["request_id"]
                            if tool == "execute_readonly_sql"
                            else None
                        ),
                    )
                except ResultLimitError as error:
                    record["outcome"] = "query_limit"
                    raise GatewayError("QUERY_LIMIT", str(error)) from None
                # The upstream string output schema requires the same bounded value in both representations.
                result = ToolResult(
                    content=text,
                    structured_content={"result": text},
                    meta={"fastmcp": {"wrap_result": True}},
                )
                record.update(
                    bytes=len(text.encode("utf-8")),
                    truncated=json.loads(text)["truncated"],
                )
            record.update(outcome="ok", rows=_row_count(result))
            return result
        except asyncio.CancelledError:
            record["outcome"] = "cancelled"
            raise
        except Exception as error:
            payload = error_payload(error, record["request_id"])
            if payload["code"] == "AUTH_REQUIRED" and not isinstance(error, GatewayError):
                # ClickHouse itself rejected the stored login; our own AUTH_REQUIRED messages stay specific.
                record["outcome"] = "login_rejected"  # P6-10 admin view; metrics still label by error_code
                payload["message"] = (
                    "The saved warehouse login for your account no longer works (its password may have changed). "
                    "Contact your administrator to update it."
                    if managed
                    else "ClickHouse rejected your username or password. Update them in this tool's settings."
                )
            if record["outcome"] == "error":
                record["outcome"] = ERROR_OUTCOMES.get(payload["code"], "error")
            record["error_code"] = payload["code"]
            raise ToolError(json.dumps(payload, separators=(",", ":"))) from None
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            gateway_metrics.registry.observe(record)
            audit.info(json.dumps(record, separators=(",", ":")))


def setup_middleware(mcp):
    if os.getenv("KAI_BINDING_MODE", "managed") not in ("managed", "manual"):
        raise RuntimeError(
            "Invalid KAI_BINDING_MODE; select managed or explicit development manual mode."
        )
    if any(
        os.getenv(flag, "false").strip().lower() == "true"
        for flag in ("CLICKHOUSE_ALLOW_WRITE_ACCESS", "CLICKHOUSE_ALLOW_DROP")
    ):
        raise RuntimeError(
            "kAI MCP is read-only; write/drop configuration is not allowed."
        )
    register_health_routes(mcp)
    gateway_metrics.register_metrics_route(mcp)
    register_warehouse_routes(mcp)
    register_readiness_routes(mcp)
    register_problem_routes(mcp)
    mcp.add_tool(Tool.from_function(get_table_schema))
    mcp.add_tool(Tool.from_function(execute_readonly_sql))
    mcp.add_tool(Tool.from_function(search_database_metadata))
    mcp.add_tool(Tool.from_function(search_rows_by_meaning))
    mcp.add_tool(Tool.from_function(suggest_comment_sql))
    mcp.add_tool(Tool.from_function(start_row_labeling))
    mcp.add_tool(Tool.from_function(get_row_labeling_job))
    register_job_routes(mcp)
    mcp.add_middleware(PerUserClickHouse())
