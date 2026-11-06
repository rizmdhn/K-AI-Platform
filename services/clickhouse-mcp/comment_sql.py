"""suggest_comment_sql (P4-04): turn drafted table/column descriptions into ALTER ... COMMENT SQL for a DBA.

kAI stays read-only: this only formats statements, after checking that the table and every named column
exist in the caller's own ClickHouse view. A DBA reviews and runs them; search_database_metadata then
uses the new comments. The descriptions themselves are drafted by the chat model (the agent's provider).
"""

import json
import re

from schema_tool import get_table_schema
from tool_errors import GatewayError

MAX_COMMENT_CHARS = 500
MAX_COLUMNS = 200


def _identifier(name):
    return "`" + name.replace("\\", "\\\\").replace("`", "\\`") + "`"


def _literal(text):
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _clean(text, what):
    if not isinstance(text, str):
        raise GatewayError("INVALID_ARGUMENT", f"The {what} description must be text.")
    text = re.sub(r"\s+", " ", re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)).strip()
    if not text or len(text) > MAX_COMMENT_CHARS:
        raise GatewayError("INVALID_ARGUMENT", f"Each description needs 1-{MAX_COMMENT_CHARS} characters ({what}).")
    return text


async def suggest_comment_sql(
    database: str, table: str, table_comment: str = "", column_comments: dict[str, str] | None = None
) -> str:
    """Format reviewed table/column descriptions as ALTER TABLE ... COMMENT statements for a DBA to run.

    Use after drafting descriptions from get_table_schema and a few sample rows. Never put sample values,
    personal data or credentials in a description. Returns statements only; nothing is executed.
    """
    column_comments = column_comments or {}
    if not table_comment and not column_comments:
        raise GatewayError("INVALID_ARGUMENT", "Provide a table description or at least one column description.")
    if not isinstance(column_comments, dict) or len(column_comments) > MAX_COLUMNS:
        raise GatewayError("INVALID_ARGUMENT", f"column_comments must map up to {MAX_COLUMNS} column names to text.")
    schema = json.loads(await get_table_schema(database, table))
    known = {column["name"]: column for column in schema["columns"]}
    unknown = sorted(set(column_comments) - set(known))
    if unknown:
        raise GatewayError("INVALID_ARGUMENT", f"These columns are not in {database}.{table}: {', '.join(unknown[:10])}.")
    target = f"{_identifier(database)}.{_identifier(table)}"
    statements = []
    if table_comment:
        statements.append(f"ALTER TABLE {target} MODIFY COMMENT {_literal(_clean(table_comment, 'table'))};")
    for name, text in column_comments.items():
        statements.append(f"ALTER TABLE {target} COMMENT COLUMN {_identifier(name)} {_literal(_clean(text, name))};")
    return json.dumps(
        {
            "database": database,
            "table": table,
            "statements": statements,
            "replaces_existing_column_comments": sorted(n for n in column_comments if known[n].get("comment")),
            "note": "Draft for review. A DBA runs these statements; kAI is read-only and executes nothing.",
        },
        ensure_ascii=False,
    )
