"""Internal, explicitly scoped schema facts; never a user-facing metadata permission grant."""

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import re

MAX_COLUMNS = 4096
MAX_BYTES = 1_048_576
SETTINGS = {
    "readonly": 1,
    "max_execution_time": 10,
    "max_result_rows": MAX_COLUMNS + 1,
    "max_result_bytes": MAX_BYTES,
    "max_memory_usage": 67_108_864,
    "max_rows_to_read": 1_000_000,
    "max_bytes_to_read": 67_108_864,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
    "read_overflow_mode": "throw",
}
POLICY_SQL = "SELECT " + ",".join(f"getSetting('{key}')" for key in SETTINGS)
SCHEMA_SQL = """SELECT t.database,t.name,t.engine,t.comment,c.name,c.type,c.comment,c.position
FROM (SELECT database,name,engine,comment FROM system.tables
      WHERE database={database:String} AND name={table:String} AND is_temporary=0) AS t
INNER JOIN (SELECT database,table,name,type,comment,position FROM system.columns
            WHERE database={database:String} AND table={table:String}) AS c
ON t.database=c.database AND t.name=c.table ORDER BY c.position"""


class SnapshotError(Exception):
    """Fixed diagnostics only; no driver exception, credentials or source metadata."""


@dataclass(frozen=True, repr=False)
class Snapshot:
    _canonical_json: str
    source_ref: str
    source_version: str

    @property
    def verified_metadata(self):
        return json.loads(self._canonical_json)


def _identifier(value):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 256
        and "\x00" not in value
    )


def _table_pair(database, table):
    return (
        _identifier(database)
        and _identifier(table)
        and database.lower() not in ("system", "information_schema")
    )


def _quoted(value):
    return "`" + value.replace("\\", "\\\\").replace("`", "\\`") + "`"


def _json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def production_client_factory(*, host, port, username, password, ca_cert=None):
    """Lazy fresh TLS clients; configuration comes only from the trusted service operator."""
    if not isinstance(host, str) or len(host) > 253:
        raise ValueError("Invalid ingestion connection configuration.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
            raise ValueError("Invalid ingestion connection configuration.") from None
    if (
        type(port) is not int
        or not 1 <= port <= 65535
        or not _identifier(username)
        or not isinstance(password, str)
        or not password
        or len(password.encode()) > 4096
        or "\x00" in password
    ):
        raise ValueError("Invalid ingestion connection configuration.")

    def connect():
        try:
            import clickhouse_connect

            return clickhouse_connect.get_client(
                host=host,
                port=port,
                username=username,
                password=password,
                database="default",
                secure=True,
                verify=True,
                ca_cert=ca_cert,  # private CA (CLICKHOUSE_CA_CERT); None = public CAs
                settings=dict(SETTINGS),
                query_limit=0,
                query_retries=0,
                connect_timeout=3,
                send_receive_timeout=15,
                autogenerate_session_id=False,
                show_clickhouse_errors=False,
            )
        except Exception:
            raise SnapshotError("SNAPSHOT_UNAVAILABLE") from None

    return connect


class SchemaCollector:
    def __init__(self, client_factory, *, source_id, tables):
        if (
            not callable(client_factory)
            or not isinstance(source_id, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", source_id)
        ):
            raise ValueError("Explicit ingestion source and client factory required.")
        pairs = list(tables)
        if not 1 <= len(pairs) <= 1024 or any(
            not isinstance(pair, (tuple, list))
            or len(pair) != 2
            or not _table_pair(*pair)
            for pair in pairs
        ):
            raise ValueError("Explicit application tables required.")
        self._factory, self._source_id = client_factory, source_id
        self._tables = frozenset(tuple(pair) for pair in pairs)

    def capture(self, database, table, *, cancelled=lambda: False):
        if not _table_pair(database, table) or (database, table) not in self._tables:
            raise SnapshotError("SNAPSHOT_UNAVAILABLE")
        client = None
        try:

            def check_cancelled():
                if cancelled():
                    raise SnapshotError("SNAPSHOT_CANCELLED")

            def query(sql, parameters=None):
                check_cancelled()
                rows = client.query(
                    sql, parameters=parameters, settings=dict(SETTINGS)
                ).result_rows
                check_cancelled()
                return rows

            check_cancelled()
            client = self._factory()
            policy = query(POLICY_SQL)
            if len(policy) != 1 or len(policy[0]) != len(SETTINGS):
                raise ValueError("Invalid effective policy")
            for (key, limit), value in zip(SETTINGS.items(), policy[0]):
                if key == "readonly" or isinstance(limit, str):
                    if str(value) != str(limit):
                        raise ValueError("Unsafe effective policy")
                elif not 0 < float(value) <= limit:
                    raise ValueError("Unbounded effective policy")

            grant_sql = f"CHECK GRANT SELECT ON {_quoted(database)}.{_quoted(table)}"

            def granted():
                rows = query(grant_sql)
                return (
                    len(rows) == 1
                    and len(rows[0]) == 1
                    and type(rows[0][0]) is int
                    and rows[0][0] == 1
                )

            if not granted():
                raise ValueError("No full-table ingestion grant")
            parameters = {"database": database, "table": table}
            first = self._snapshot(query(SCHEMA_SQL, parameters), database, table)
            second = self._snapshot(query(SCHEMA_SQL, parameters), database, table)
            if first.source_version != second.source_version or not granted():
                raise ValueError("Metadata changed or grant revoked")
            check_cancelled()
            return first
        except SnapshotError:
            raise
        except Exception:
            raise SnapshotError("SNAPSHOT_UNAVAILABLE") from None
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    raise SnapshotError("SNAPSHOT_UNAVAILABLE") from None

    def _snapshot(self, rows, database, table):
        if not 1 <= len(rows) <= MAX_COLUMNS:
            raise ValueError("Incomplete or oversized schema")
        columns, positions, names = [], set(), set()
        header = None
        for row in rows:
            if len(row) != 8 or row[0] != database or row[1] != table:
                raise ValueError("Wrong schema object")
            _, _, engine, table_comment, name, column_type, comment, position = row
            if (
                not _identifier(engine)
                or not _identifier(name)
                or not isinstance(column_type, str)
                or not column_type.strip()
                or len(column_type.encode()) > 65_536
                or any(
                    not isinstance(text, str)
                    or "\x00" in text
                    or len(text.encode()) > 65_536
                    for text in (table_comment, comment)
                )
                or type(position) is not int
                or position < 1
                or position > MAX_COLUMNS
                or position in positions
                or name in names
            ):
                raise ValueError("Invalid schema facts")
            if header is None:
                header = (engine, table_comment)
            elif header != (engine, table_comment):
                raise ValueError("Mixed table metadata")
            positions.add(position)
            names.add(name)
            columns.append(
                {
                    "name": name,
                    "type": column_type,
                    "comment": comment,
                    "position": position,
                }
            )
        if positions != set(range(1, len(columns) + 1)):
            raise ValueError("Incomplete column metadata")
        payload = {
            "snapshot_format": 1,
            "source_id": self._source_id,
            "database": database,
            "table": table,
            "engine": header[0],
            "comment": header[1],
            "columns": sorted(columns, key=lambda column: column["position"]),
        }
        canonical = _json(payload)
        encoded = canonical.encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise ValueError("Schema exceeds byte limit")
        return Snapshot(
            canonical,
            _json([self._source_id, database, table]),
            "ch-schema-v1:" + hashlib.sha256(encoded).hexdigest(),
        )
