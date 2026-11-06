"""Process-local, bounded-cardinality operational metrics. No identity/query labels."""

import hmac
import math
import os
import re
import sys
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from starlette.responses import PlainTextResponse, Response

TOOLS = {
    "list_databases",
    "list_tables",
    "run_query",
    "get_table_schema",
    "execute_readonly_sql",
}
ERRORS = {
    "AUTH_REQUIRED": "auth_denied",
    "ACCESS_DENIED": "access_denied",
    "UNSAFE_SQL": "sql_denied",
    "INVALID_ARGUMENT": "invalid_argument",
    "QUERY_LIMIT": "query_limit",
    "DEPENDENCY_UNAVAILABLE": "dependency_error",
}


def number(value):
    return (
        value
        if type(value) in (int, float) and math.isfinite(value) and value >= 0
        else 0
    )


class Metrics:
    def __init__(self):
        self.started = time.monotonic()
        self.values = {}
        self.lock = threading.Lock()

    def observe(self, record):
        tool = record.get("tool") if record.get("tool") in TOOLS else "other"
        outcome = (
            "cancelled"
            if record.get("outcome") == "cancelled"
            else (
                "ok"
                if record.get("outcome") == "ok"
                else ERRORS.get(record.get("error_code"), "error")
            )
        )
        with self.lock:
            values = self.values.setdefault((tool, outcome), [0, 0.0, 0, 0, 0])
            values[0] += 1
            values[1] += number(record.get("duration_ms")) / 1000
            values[2] += number(record.get("rows"))
            values[3] += number(record.get("bytes"))
            values[4] += int(record.get("truncated") is True)

    def render(self):
        with self.lock:
            snapshot = [
                (key, tuple(values)) for key, values in sorted(self.values.items())
            ]
        lines = [
            "# HELP kai_mcp_uptime_seconds MCP process uptime; counters reset on restart.",
            "# TYPE kai_mcp_uptime_seconds gauge",
            f"kai_mcp_uptime_seconds {time.monotonic()-self.started:g}",
        ]
        families = [
            ("tool_calls_total", "Completed MCP tool calls.", "counter", 0),
            (
                "tool_duration_seconds",
                "Gateway duration including validation, lookup and execution.",
                "summary",
                1,
            ),
            ("returned_rows_total", "Returned rows when available.", "counter", 2),
            (
                "returned_bytes_total",
                "Bounded SQL response bytes when available.",
                "counter",
                3,
            ),
            (
                "truncated_results_total",
                "SQL results truncated by the gateway.",
                "counter",
                4,
            ),
        ]
        for suffix, help_text, kind, index in families:
            name = "kai_mcp_" + suffix
            lines.extend([f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"])
            for (tool, outcome), values in snapshot:
                labels = f'{{tool="{tool}",outcome="{outcome}"}}'
                if kind == "summary":
                    lines.extend(
                        [
                            f"{name}_sum{labels} {values[index]:.12g}",
                            f"{name}_count{labels} {values[0]}",
                        ]
                    )
                else:
                    lines.append(f"{name}{labels} {values[index]:g}")
        return "\n".join(lines) + "\n"


registry = Metrics()


async def scrape(request):
    token = os.getenv("KAI_METRICS_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token) or any(
        hmac.compare_digest(token.encode(), os.getenv(other, "").encode())
        for other in ("CLICKHOUSE_MCP_AUTH_TOKEN", "KAI_PLATFORM_INTERNAL_TOKEN")
    ):
        return PlainTextResponse(
            "Metrics are not configured.",
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )
    supplied = request.headers.get("authorization", "")
    if len(supplied) > 8192 or not hmac.compare_digest(
        supplied.encode(), ("Bearer " + token).encode()
    ):
        return PlainTextResponse(
            "Metrics authentication required.",
            status_code=401,
            headers={"Cache-Control": "no-store", "WWW-Authenticate": "Bearer"},
        )
    return Response(
        registry.render(),
        headers={
            "Content-Type": "text/plain; version=0.0.4; charset=utf-8",
            "Cache-Control": "no-store",
        },
    )


def register_metrics_route(mcp):
    mcp.custom_route("/metrics", methods=["GET"])(scrape)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def scrape_local(token):
    request = Request(
        "http://127.0.0.1:8000/metrics", headers={"Authorization": "Bearer " + token}
    )
    with build_opener(ProxyHandler({}), NoRedirect()).open(
        request, timeout=5
    ) as response:
        data = response.read(262145)
        if len(data) > 262144:
            raise ValueError("Metrics response is too large.")
    return data.decode()


if __name__ == "__main__":
    token = os.getenv("KAI_METRICS_TOKEN", "")
    if not token:
        sys.exit("Metrics are not configured. Run make metrics-up first.")
    try:
        print(scrape_local(token), end="")
    except Exception:
        sys.exit(
            "Metrics scrape failed; credentials and backend diagnostics suppressed."
        )
