"""End-to-end security checks for the DBA roles in Docs/user_matrix.md (TDD SEC-01/02/03/08/09/17).

Skipped unless MCP_E2E_URL is set. Same throwaway setup as test_e2e_per_user.py, plus:
  docker exec -i ch-e2e clickhouse-client --password admin --multiquery < tests/e2e_user_matrix.sql
Every account goes through kAI's gateway exactly as a bound user would (manual mode headers).
"""
import json
import unittest

from test_e2e_per_user import URL, call


def as_user(name):
    return {"X-CH-User": f"u_{name}", "X-CH-Password": f"{name}-pw"}


def run(name, sql):
    failed, text = call("run_query", {"query": sql}, as_user(name))
    return failed, (json.loads(text)["rows"] if not failed else json.loads(text))


@unittest.skipUnless(URL, "set MCP_E2E_URL to run against a live mcp-clickhouse")
class UserMatrixEndToEnd(unittest.TestCase):
    def assert_denied(self, name, sql, code="ACCESS_DENIED"):
        failed, payload = run(name, sql)
        self.assertTrue(failed, f"{name} should be refused: {sql}")
        self.assertEqual(payload["code"], code, payload)
        self.assertNotIn("Penumpang Fiktif", json.dumps(payload))

    def test_sec01_each_role_reads_what_it_is_granted(self):
        for name, sql in (
            ("analyst", "SELECT sum(amount) FROM gold.penjualan"),
            ("steward", "SELECT count() FROM raw.events"),
            ("engineer", "SELECT count() FROM raw.events"),
            ("pipeline", "SELECT count() FROM silver.rute_ref"),
            ("bi", "SELECT kota FROM silver.rute_ref"),
        ):
            with self.subTest(name=name):
                failed, rows = run(name, sql)
                self.assertFalse(failed, rows)

    def test_sec02_tables_outside_the_grant_are_refused(self):
        self.assert_denied("analyst", "SELECT * FROM raw.events")
        self.assert_denied("analyst", "SELECT * FROM gold.penumpang_detail")  # revoked from analysts
        self.assert_denied("bi", "SELECT * FROM silver.internal")
        self.assert_denied("pipeline", "SELECT * FROM gold.penjualan")

    def test_sec03_bi_column_grant_hides_personal_data(self):
        failed, rows = run("bi", "SELECT id, rute, tanggal FROM gold.penumpang_detail")
        self.assertFalse(failed, rows)
        self.assertEqual(rows[0][:2], [1, "CGK-DPS"])
        self.assert_denied("bi", "SELECT nama FROM gold.penumpang_detail")
        self.assert_denied("bi", "SELECT * FROM gold.penumpang_detail")

    def test_sec08_write_roles_stay_read_only_through_kai(self):
        for name in ("engineer", "pipeline"):
            for sql in ("INSERT INTO raw.events VALUES (9, 'x')", "ALTER TABLE raw.events DELETE WHERE 1",
                        "DROP TABLE raw.events", "SELECT 1; DROP TABLE raw.events"):
                with self.subTest(name=name, sql=sql):
                    self.assert_denied(name, sql, code="UNSAFE_SQL")
            failed, rows = run(name, "SELECT getSetting('readonly')")
            self.assertFalse(failed, rows)
            self.assertEqual(str(rows[0][0]), "1", "writable roles run with readonly=1")
        failed, rows = run("steward", "SELECT count() FROM raw.events")
        self.assertEqual(rows, [[2]], "no write reached the table")

    def test_sec09_external_reads_and_exports_are_refused(self):
        for sql in ("SELECT * FROM url('http://example.invalid/x.csv', CSV, 'a String')",
                    "SELECT * FROM gold.penjualan INTO OUTFILE '/tmp/x.csv'",
                    "SELECT * FROM file('/etc/passwd', 'LineAsString')"):
            with self.subTest(sql=sql):
                self.assert_denied("steward", sql, code="UNSAFE_SQL")

    def test_analyst_profile_limits_apply(self):
        failed, rows = run("analyst", "SELECT getSetting('readonly'), getSetting('max_execution_time') FROM gold.penjualan LIMIT 1")
        self.assertFalse(failed, rows)
        self.assertEqual([float(v) for v in rows[0]], [2, 300])

    def test_sec12_metadata_search_returns_only_visible_tables(self):
        failed, text = call("search_database_metadata", {"query": "data penumpang dan rute penerbangan", "limit": 20}, as_user("analyst"))
        self.assertFalse(failed, text)
        found = {(r["database"], r["table"]) for r in json.loads(text)["results"]}
        self.assertIn(("gold", "penjualan"), found)
        self.assertNotIn(("gold", "penumpang_detail"), found, "revoked table must not be discoverable")
        # data_analyst also holds sales.orders from the base fixture; raw/bronze/silver must never appear.
        self.assertFalse({db for db, _ in found} & {"raw", "bronze", "silver"}, f"analyst grants only: {found}")
        failed, text = call("search_database_metadata", {"query": "nama penumpang", "limit": 3}, as_user("steward"))
        self.assertEqual(json.loads(text)["results"][0]["table"], "penumpang_detail")

    def test_af03_row_search_runs_as_the_caller_with_the_sql_policy(self):
        args = {"sql": "SELECT id, rute FROM gold.penumpang_detail", "query": "penerbangan ke Bali"}
        failed, text = call("search_rows_by_meaning", args, as_user("bi"))
        self.assertFalse(failed, text)
        self.assertEqual(json.loads(text)["rows"][0][:2], [1, "CGK-DPS"])
        failed, text = call("search_rows_by_meaning", {**args, "sql": "SELECT id, nama FROM gold.penumpang_detail"}, as_user("bi"))
        self.assertTrue(failed, "column outside bi's grant")
        self.assertNotIn("Penumpang Fiktif", text)
        failed, text = call("search_rows_by_meaning", args, as_user("analyst"))
        self.assertTrue(failed, "table revoked from analyst")
        failed, text = call("search_rows_by_meaning", {**args, "sql": "SELECT * FROM file('/etc/passwd', 'LineAsString')"}, as_user("steward"))
        self.assertEqual(json.loads(text)["code"], "UNSAFE_SQL")

    def test_infra_dba_system_tables_stay_blocked_by_policy(self):
        # Owner decision pending (Backlog note "infra_dba access to system.*"); today kAI blocks system.*.
        self.assert_denied("dba", "SELECT count() FROM system.query_log", code="UNSAFE_SQL")
        self.assert_denied("dba", "KILL QUERY WHERE 1", code="UNSAFE_SQL")


if __name__ == "__main__":
    unittest.main()
