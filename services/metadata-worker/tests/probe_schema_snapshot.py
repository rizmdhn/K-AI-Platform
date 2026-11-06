"""Runs only inside the disposable internal-network fixture, never against configured env."""
import sys
import unittest

sys.path.insert(0, '/worker')
import clickhouse_connect
from schema_snapshot import SchemaCollector, SnapshotError, SETTINGS


def connect(username='ingest'):
    # HTTP is confined to this test-only Docker network; production factory has no TLS bypass.
    return clickhouse_connect.get_client(host='warehouse', port=8123, username=username,
                                        password='fixture-only', secure=False, settings=dict(SETTINGS),
                                        query_retries=0, connect_timeout=3, send_receive_timeout=15,
                                        show_clickhouse_errors=False, autogenerate_session_id=False)


class RealClickHouseTests(unittest.TestCase):
    def setUp(self):
        self.admin = clickhouse_connect.get_client(host='warehouse', port=8123, username='default',
                                                   password='fixture-admin-only', show_clickhouse_errors=False)
        self.addCleanup(self.admin.close)
        self.admin.command('GRANT SELECT ON sales.orders TO ingest')
        self.admin.command("ALTER TABLE sales.orders MODIFY COMMENT 'Pesanan'")

    def collector(self, username='ingest', table='orders', factory=None):
        return SchemaCollector(factory or (lambda: connect(username)), source_id='fixture', tables=[('sales', table)])

    def test_facts_hash_and_comment_change(self):
        first = self.collector().capture('sales', 'orders')
        self.assertEqual(first.verified_metadata['comment'], 'Pesanan')
        self.assertEqual([(c['name'], c['type'], c['comment']) for c in first.verified_metadata['columns']],
                         [('region', 'String', 'Wilayah'), ('amount', 'UInt32', '')])
        self.assertEqual(first.source_version, self.collector().capture('sales', 'orders').source_version)
        self.admin.command("ALTER TABLE sales.orders MODIFY COMMENT 'Baru'")
        self.assertNotEqual(first.source_version, self.collector().capture('sales', 'orders').source_version)

    def test_table_and_column_only_accounts_cannot_borrow_ingestion_visibility(self):
        for username in ('other', 'columns_only'):
            with self.subTest(username=username), self.assertRaises(SnapshotError):
                self.collector(username).capture('sales', 'orders')
        for table in ('secret', 'missing'):
            with self.subTest(table=table), self.assertRaises(SnapshotError):
                self.collector(table=table).capture('sales', table)

    def test_exact_identifier_with_metacharacters(self):
        name = "order_%\\`'; DROP TABLE x;--"
        snapshot = self.collector(table=name).capture('sales', name)
        self.assertEqual(snapshot.verified_metadata['table'], name)
        self.assertEqual(snapshot.verified_metadata['columns'][0]['name'], 'literal')

    def test_session_is_readonly_even_with_fixture_insert_grant(self):
        client = connect()
        self.addCleanup(client.close)
        self.assertEqual(client.query("SELECT getSetting('readonly')").result_rows[0][0], 1)
        with self.assertRaises(Exception): client.command("INSERT INTO sales.orders VALUES ('blocked', 1)")

    def test_revocation_mid_capture_never_publishes_snapshot(self):
        client = connect()
        query = client.query
        first_read = True
        def revoke(sql, *args, **kwargs):
            nonlocal first_read
            result = query(sql, *args, **kwargs)
            if sql.startswith('SELECT t.database') and first_read:
                first_read = False
                self.admin.command('REVOKE SELECT ON sales.orders FROM ingest')
            return result
        client.query = revoke
        with self.assertRaises(SnapshotError):
            self.collector(factory=lambda: client).capture('sales', 'orders')
        with self.assertRaises(SnapshotError): self.collector().capture('sales', 'orders')

    def test_schema_change_mid_capture_never_publishes_snapshot(self):
        client = connect()
        query = client.query
        first_read = True
        def change(sql, *args, **kwargs):
            nonlocal first_read
            result = query(sql, *args, **kwargs)
            if sql.startswith('SELECT t.database') and first_read:
                first_read = False
                self.admin.command("ALTER TABLE sales.orders MODIFY COMMENT 'Changed during capture'")
            return result
        client.query = change
        with self.assertRaises(SnapshotError):
            self.collector(factory=lambda: client).capture('sales', 'orders')

    def test_oversized_comments_fail_without_echoing_facts(self):
        self.admin.command("ALTER TABLE sales.orders MODIFY COMMENT '" + 'private-' * 9000 + "'")
        with self.assertRaises(SnapshotError) as caught: self.collector().capture('sales', 'orders')
        self.assertEqual(str(caught.exception), 'SNAPSHOT_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main(verbosity=2)
