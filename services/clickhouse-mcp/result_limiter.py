"""Bound the JSON query payload sent to a model, including its metadata."""

import json

MAX_RESULT_ROWS = 1000
MAX_RESULT_BYTES = 1_048_576


class ResultLimitError(ValueError):
    """Safe error message containing no upstream values."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _tail(count, truncated):
    return (
        '],"truncated":'
        + ("true" if truncated else "false")
        + ',"row_count":'
        + str(count)
        + "}"
    )


def limit_result(
    text, *, max_rows=MAX_RESULT_ROWS, max_bytes=MAX_RESULT_BYTES, request_id=None
):
    """Keep a prefix of complete rows within both caps; reject oversized columns.

    This is an output limit. Upstream has already materialized the database result.
    """
    if (
        type(max_rows) is not int
        or max_rows < 1
        or type(max_bytes) is not int
        or max_bytes < 1
    ):
        raise ValueError("Result limits must be positive integers")
    try:
        payload = json.loads(text)
        columns, rows = payload["columns"], payload["rows"]
        if (
            not isinstance(columns, list)
            or not all(isinstance(c, str) for c in columns)
            or not isinstance(rows, list)
            or not all(isinstance(row, list) for row in rows)
        ):
            raise ValueError
        prefix = '{"columns":' + _json(columns) + ","
        if request_id is not None:
            prefix += '"request_id":' + _json(request_id) + ","
        prefix += '"rows":['
        used = len(prefix.encode("utf-8"))
        if used + len(_tail(0, bool(rows))) > max_bytes:
            raise ResultLimitError(
                "QUERY_LIMIT: Result columns exceed the output byte limit."
            )
        encoded_rows = []
        for row in rows[:max_rows]:
            encoded = _json(row)
            count = len(encoded_rows) + 1
            size = len(encoded.encode("utf-8")) + (1 if encoded_rows else 0)
            if used + size + len(_tail(count, count < len(rows))) > max_bytes:
                break
            encoded_rows.append(encoded)
            used += size
        return (
            prefix
            + ",".join(encoded_rows)
            + _tail(len(encoded_rows), len(encoded_rows) < len(rows))
        )
    except ResultLimitError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ResultLimitError("QUERY_LIMIT: Invalid query result format.") from None
