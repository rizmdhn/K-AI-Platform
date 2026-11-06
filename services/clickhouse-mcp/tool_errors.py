"""Client-safe tool errors; raw backend exception text never crosses this boundary."""

from fastmcp.exceptions import ToolError, ValidationError


class GatewayError(ToolError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def error_payload(error, request_id):
    if isinstance(error, GatewayError):
        code, message = error.code, str(error)
    elif isinstance(error, ValidationError):
        code, message = "INVALID_ARGUMENT", "The tool arguments are invalid."
    else:
        text = str(error).upper()
        if "ACCESS_DENIED" in text:
            code, message = (
                "ACCESS_DENIED",
                "Your ClickHouse role does not allow this data. Ask your administrator if you need access.",
            )
        elif "AUTHENTICATION_FAILED" in text:
            code, message = (
                "AUTH_REQUIRED",
                "ClickHouse rejected the saved username or password.",
            )
        elif any(
            reason in text
            for reason in (
                "MEMORY_LIMIT_EXCEEDED",
                "TOO_MANY_ROWS",
                "TOO_MANY_BYTES",
                "QUOTA_EXCEEDED",
                "TIMEOUT_EXCEEDED",
                "TIMED OUT",
            )
        ):
            code, message = (
                "QUERY_LIMIT",
                "The query hit a database limit (too many rows, too much memory or too slow). "
                "Narrow it with filters, a shorter date range or aggregation.",
            )
        elif any(
            reason in text
            for reason in (
                "UNKNOWN_DATABASE",
                "UNKNOWN_TABLE",
                "UNKNOWN_IDENTIFIER",
                "SYNTAX_ERROR",
                "BAD_ARGUMENTS",
            )
        ):
            code, message = (
                "INVALID_ARGUMENT",
                "ClickHouse rejected the query. Check the table and column names.",
            )
        else:
            code, message = (
                "DEPENDENCY_UNAVAILABLE",
                "The data warehouse is unavailable right now. Try again in a few minutes; "
                "if it continues, contact your administrator.",
            )
    return {"code": code, "message": message, "request_id": request_id}


ERROR_OUTCOMES = {
    "AUTH_REQUIRED": "auth_failed",
    "ACCESS_DENIED": "access_denied",
    "UNSAFE_SQL": "unsafe_sql",
    "QUERY_LIMIT": "query_limit",
}
