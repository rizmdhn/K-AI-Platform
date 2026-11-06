import unittest

from sql_policy import MAX_SQL_CHARS, UnsafeSQL, validate

ALLOWED = [
    "SELECT 1",
    "SELECT 1;",
    "select count() from analytics.orders",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT * FROM a UNION ALL SELECT * FROM b",
    "SELECT * FROM t FINAL SAMPLE 0.1 ARRAY JOIN arr LIMIT 10 BY a",
    "SELECT count() FROM db.t PREWHERE x > 1 GROUP BY a WITH TOTALS",
    "SELECT toStartOfMonth(d) m, sumIf(v, k = 1) FROM t GROUP BY m ORDER BY m",
    "SELECT * FROM t WHERE note = 'DROP TABLE x; SETTINGS readonly=0'",
    "SELECT dictGet('d', 'a', toUInt64(1))",
    "SELECT * FROM t WHERE a IN (1, 2, 3)",
    "SELECT * FROM t WHERE (a, b) IN ((1, 2))",
    "SELECT * FROM t WHERE a IN (SELECT a FROM t2)",
    "SELECT * FROM t WHERE a = 1 /* ; DROP TABLE t */",
]

# Attack corpus: every entry must be rejected.
DENIED = [
    "",
    "   ",
    "SELECT 1; DROP TABLE t",
    "SELECT 1; SELECT 2",
    "SELECT 1; -- trailing comment after a second statement boundary",
    "DROP TABLE t",
    "INSERT INTO t SELECT 1",
    "ALTER TABLE t DELETE WHERE 1",
    "ALTER TABLE t UPDATE x = 1 WHERE 1",
    "CREATE TABLE x (a Int8) ENGINE = Memory",
    "TRUNCATE TABLE t",
    "GRANT ALL ON *.* TO x",
    "REVOKE SELECT ON t FROM x",
    "SYSTEM SHUTDOWN",
    "SET readonly = 0",
    "USE other",
    "SHOW TABLES",
    "DESCRIBE t",
    "EXPLAIN SELECT 1",
    "OPTIMIZE TABLE t FINAL",
    "KILL QUERY WHERE 1",
    "SELECT * FROM t SETTINGS max_execution_time = 1000",
    "SELECT * FROM (SELECT * FROM t SETTINGS readonly = 0)",
    "SELECT * FROM t FORMAT CSV",
    "SELECT * FROM t INTO OUTFILE '/tmp/x'",
    "SELECT * FROM url('http://evil/x', CSV, 'a String')",
    "SELECT * FROM s3('https://bucket/x')",
    "SELECT * FROM remote('host', db.t)",
    "SELECT * FROM numbers(10)",
    "SELECT * FROM (SELECT * FROM url('http://x', CSV))",
    "SELECT * FROM t WHERE x IN (SELECT * FROM s3('x'))",
    "SELECT * FROM t JOIN url('http://x', CSV) u ON 1",
    "WITH x AS (SELECT * FROM mysql('h', 'db', 't', 'u', 'p')) SELECT * FROM x",
    "SELECT file('/etc/passwd')",
    "SELECT sleep(3)",
    "SELECT * FROM system.users",
    "SELECT * FROM SYSTEM.query_log",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM `system`.users",
    "SELECT * FROM t1, system.users",
    "SELECT 1 WHERE 1 IN system.users",
    "SELECT 1 WHERE 1 GLOBAL IN system.users",
    "SELECT 1 WHERE 1 IN (system.users)",
    "SELECT 1 WHERE 1 IN url('http://x', CSV, 'a Int')",
    "SELECT * FROM merge('system', '.*')",
    "SELECT * FROM {tbl:Identifier}",
    "SELECT 1 UNION ALL SELECT 2 SETTINGS max_threads = 1",
    "SELECT catboostEvaluate('/x/model.bin', 1)",
    "SELECT * FROM t; /* x */",
    "SELECT * FROM t WHERE (",
    "SELECT " + "(" * 2000 + "1" + ")" * 2000,
    "SELECT '" + "a" * MAX_SQL_CHARS + "'",
]


class SqlPolicyTests(unittest.TestCase):
    def test_allows_read_queries(self):
        for sql in ALLOWED:
            with self.subTest(sql=sql):
                validate(sql)

    def test_denies_attack_corpus(self):
        for sql in DENIED:
            with self.subTest(sql=sql[:80]):
                with self.assertRaises(UnsafeSQL):
                    validate(sql)

    def test_rejects_non_string(self):
        with self.assertRaises(UnsafeSQL):
            validate(None)


if __name__ == "__main__":
    unittest.main()
