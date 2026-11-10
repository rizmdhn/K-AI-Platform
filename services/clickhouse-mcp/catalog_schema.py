"""Shape of an AI metadata draft before it becomes OpenMetadata suggestions (FR-08).

Only a table description and descriptions for real, still-empty columns; bounded text. Unknown = left out,
never invented. Pure stdlib so the metadata worker can import it from the MCP image.
"""

MAX_DESCRIPTION, MAX_COLUMN = 2000, 300


def _text(value, limit):
    return " ".join(value.replace("\x00", "").split())[:limit] if isinstance(value, str) else ""


def clean_metadata(raw, column_names, want_description=True):
    """Validated subset of raw; raises ValueError when nothing usable is left."""
    if not isinstance(raw, dict):
        raise ValueError("metadata must be an object")
    names = set(column_names)
    columns = raw.get("columns") if isinstance(raw.get("columns"), dict) else {}
    result = {
        "description": _text(raw.get("description"), MAX_DESCRIPTION) if want_description else "",
        "columns": {n: t for n, v in columns.items() if n in names and (t := _text(v, MAX_COLUMN))},
    }
    result = {k: v for k, v in result.items() if v}
    if not result:
        raise ValueError("nothing usable in the draft")
    return result
