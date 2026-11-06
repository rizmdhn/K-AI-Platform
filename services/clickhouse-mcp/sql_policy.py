"""Read-only SQL policy for run_query (TDD §5.2).

Parsing decides whether a query is *shaped* safely. It is not authorization:
ClickHouse grants, row policies, readonly=1 and the DBA settings profile
(P3-03, D05) remain the enforcement layer.

Test: cd services/clickhouse-mcp && python -m unittest discover -s tests
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

MAX_SQL_CHARS = 20_000

# Any of these node types anywhere in the tree means the query is not a plain read.
FORBIDDEN_NODES = (
    exp.DDL,
    exp.DML,
    exp.Command,
    exp.Set,
    exp.Use,
    exp.Describe,
    exp.Kill,
    exp.TruncateTable,
    exp.Pragma,
    exp.Grant,
    exp.Revoke,
    exp.Cache,
    exp.Uncache,
    exp.LoadData,
    exp.Into,
)
FORBIDDEN_CLAUSES = ("settings", "format")
FORBIDDEN_DATABASES = {"system", "information_schema"}
# ponytail: small scalar denylist (file access, stalling). Table functions are blocked
# wholesale below; add names here if a version-specific function proves risky.
FORBIDDEN_FUNCTIONS = {"file", "sleep", "sleepeachrow", "catboostevaluate"}


class UnsafeSQL(ValueError):
    pass


def validate(sql: str) -> None:
    """Raise UnsafeSQL, with a reason safe to show the model, unless sql is one plain read."""
    if not isinstance(sql, str) or not sql.strip():
        raise UnsafeSQL("SQL is empty.")
    if len(sql) > MAX_SQL_CHARS:
        raise UnsafeSQL(f"SQL is longer than {MAX_SQL_CHARS} characters.")

    try:
        statements = [s for s in sqlglot.parse(sql, read="clickhouse") if s is not None]
    except (SqlglotError, RecursionError):
        raise UnsafeSQL(
            "SQL could not be parsed as supported ClickHouse syntax."
        ) from None
    if len(statements) != 1:
        raise UnsafeSQL("Exactly one statement is allowed.")

    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        raise UnsafeSQL("Only SELECT queries are allowed.")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSQL("Only read-only SELECT queries are allowed.")
        for clause in FORBIDDEN_CLAUSES:
            if node.args.get(clause):
                raise UnsafeSQL(f"{clause.upper()} clauses are not allowed.")
        if isinstance(node, exp.Table):
            if not isinstance(node.this, exp.Identifier):
                raise UnsafeSQL("Table functions are not allowed.")
            if node.db.lower() in FORBIDDEN_DATABASES:
                raise UnsafeSQL(
                    "System and information_schema tables are not available here."
                )
        # ClickHouse reads `x IN db.table` / `x IN url(...)` as a table source, but sqlglot
        # parses the right side as a column or function, so the Table checks never see it.
        if isinstance(node, exp.In) and (
            node.args.get("field")
            or any(isinstance(e, exp.Column) for e in node.expressions)
        ):
            raise UnsafeSQL(
                "Use IN (SELECT ...) or a list of constants on the right side of IN."
            )
        if isinstance(node, exp.Func):
            name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
            if name.lower() in FORBIDDEN_FUNCTIONS:
                raise UnsafeSQL(f"Function {name} is not allowed.")

    # ponytail: mcp-clickhouse runs the caller's text, so a sqlglot/ClickHouse parser
    # differential is possible; readonly=1 + CH grants are the backstop.
