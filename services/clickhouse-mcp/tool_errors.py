"""Client-safe tool errors; raw backend exception text never crosses this boundary.

Messages are in Indonesian because agents may show them verbatim; logic keys on `code`, never on the text.
"""

from fastmcp.exceptions import ToolError, ValidationError


class GatewayError(ToolError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def error_payload(error, request_id):
    if isinstance(error, GatewayError):
        code, message = error.code, str(error)
    elif isinstance(error, ValidationError):
        code, message = "INVALID_ARGUMENT", "Argumen tool tidak valid."
    else:
        text = str(error).upper()
        if "ACCESS_DENIED" in text:
            code, message = (
                "ACCESS_DENIED",
                "Hak akses ClickHouse Anda tidak mencakup data ini. Hubungi administrator bila Anda memerlukan akses.",
            )
        elif "AUTHENTICATION_FAILED" in text:
            code, message = (
                "AUTH_REQUIRED",
                "ClickHouse menolak username atau password yang tersimpan. Hubungi administrator untuk memperbaruinya.",
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
                "Query melewati batas database (terlalu banyak baris, memori, atau waktu). "
                "Persempit dengan filter, rentang tanggal yang lebih pendek, atau agregasi.",
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
                "ClickHouse menolak query. Periksa nama tabel dan kolom.",
            )
        else:
            code, message = (
                "DEPENDENCY_UNAVAILABLE",
                "Data warehouse sedang tidak dapat diakses. Coba lagi beberapa menit lagi; "
                "bila berlanjut, hubungi administrator.",
            )
    return {"code": code, "message": message, "request_id": request_id}


ERROR_OUTCOMES = {
    "AUTH_REQUIRED": "auth_failed",
    "ACCESS_DENIED": "access_denied",
    "UNSAFE_SQL": "unsafe_sql",
    "QUERY_LIMIT": "query_limit",
}
