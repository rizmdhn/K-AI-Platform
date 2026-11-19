"""Audit table writer and paging helpers that need no database: sanitizing, cursors, a full queue."""
import json
import logging
import unittest
import unittest.mock

import audit_store

A = "a" * 24


def line(**fields):
    return json.dumps({"librechat_user": A, "tool": "execute_readonly_sql", "outcome": "ok", "ch_user": "alice_ch",
                       "sql_sha256": "f" * 64, "rows": 3, "duration_ms": 12, **fields})


class RowTests(unittest.TestCase):
    def test_keeps_only_columns_and_cleans_values(self):
        row = dict(zip(audit_store.COLUMNS, audit_store.to_row(line(outcome="made_up", tool="Bad;Tool", rows=True,
                                                                     ch_user="x" * 300), 0)))
        self.assertEqual((row["outcome"], row["tool"], row["rows"], len(row["ch_user"])), ("error", None, None, 256))
        self.assertNotIn("f" * 64, json.dumps(row, default=str))  # the SQL hash stays in audit.jsonl only
        self.assertEqual(row["ts"].isoformat(), "1970-01-01T00:00:00+00:00")

    def test_skips_lines_without_a_platform_user(self):
        for message in ("garbage {", line(librechat_user="not-a-user"), json.dumps(["x"])):
            self.assertIsNone(audit_store.to_row(message, 0))

    def test_cursor_round_trip_and_bad_tokens(self):
        self.assertEqual(audit_store.decode_cursor(audit_store.encode_cursor(12345)), 12345)
        for bad in ("!!", "LTE", audit_store.encode_cursor("1 OR 1=1")):
            self.assertIsNone(audit_store.decode_cursor(bad))


class WriterTests(unittest.TestCase):
    def test_full_queue_drops_oldest_and_counts(self):
        writer = audit_store.Writer("dsn", lambda: 90, start=False, size=2)
        logger = logging.getLogger("kai.audit.test")
        logger.addHandler(writer)
        logger.setLevel(logging.INFO)
        for n in range(3):
            logger.info(line(rows=n))
        logger.info("not json")
        self.assertEqual(writer.dropped, 1)
        self.assertEqual([r[audit_store.COLUMNS.index("rows")] for r in list(writer.queue.queue)], [1, 2])

    def test_database_down_drops_batch_after_retries(self):
        def down(dsn):
            raise OSError("dsn secret")
        writer = audit_store.Writer("dsn", lambda: 90, connect=down, start=False)
        with unittest.mock.patch.object(audit_store.time, "sleep"), self.assertLogs(audit_store.log, "WARNING") as logs:
            self.assertFalse(writer.flush_rows([audit_store.to_row(line(), 0)]))
        self.assertEqual(writer.dropped, 1)
        self.assertNotIn("dsn secret", "".join(logs.output))


if __name__ == "__main__":
    unittest.main()
