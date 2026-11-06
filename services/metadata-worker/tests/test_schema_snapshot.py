import copy
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def records(table='orders', database='sales'):
    return [(database, table, 'Memory', 'Pesanan', 'region', 'String', 'Wilayah', 1),
            (database, table, 'Memory', 'Pesanan', 'amount', 'UInt32', '', 2)]


class Client:
    def __init__(self, rows=None):
        self.rows = records() if rows is None else rows
        self.calls = []
        self.closed = False
        self.grants = [1, 1]
        self.policy = [(1, 10, 4097, 1048576, 67108864, 1000000, 67108864,
                        'throw', 'throw', 'throw')]
        self.reads = None
        self.row_lists = False

    def query(self, sql, parameters=None, settings=None):
        self.calls.append((sql, parameters, settings))
        if sql.startswith('SELECT getSetting'):
            rows = self.policy
        elif sql.startswith('CHECK GRANT'):
            rows = [(self.grants.pop(0),)]
        else:
            rows = self.reads.pop(0) if self.reads else self.rows
        return SimpleNamespace(result_rows=[list(row) for row in rows] if self.row_lists else rows)

    def close(self):
        self.closed = True


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('schema_snapshot'),
                             'Schema snapshot collector is not implemented')
        self.module = importlib.import_module('schema_snapshot')

    def collector(self, client, tables=(('sales', 'orders'),), source_id='fixture'):
        return self.module.SchemaCollector(lambda: client, source_id=source_id, tables=tables)

    def test_returns_only_facts_with_stable_hash_and_isolated_copies(self):
        client = Client()
        first = self.collector(client).capture('sales', 'orders')
        facts = first.verified_metadata
        self.assertEqual(facts, {'snapshot_format': 1, 'source_id': 'fixture',
                                 'database': 'sales', 'table': 'orders', 'engine': 'Memory',
                                 'comment': 'Pesanan', 'columns': [
                                     {'name': 'region', 'type': 'String', 'comment': 'Wilayah', 'position': 1},
                                     {'name': 'amount', 'type': 'UInt32', 'comment': '', 'position': 2}]})
        facts['columns'][0]['name'] = 'mutated'
        self.assertEqual(first.verified_metadata['columns'][0]['name'], 'region')
        second = self.collector(Client(list(reversed(records())))).capture('sales', 'orders')
        self.assertEqual(first.source_version, second.source_version)
        self.assertRegex(first.source_version, r'^ch-schema-v1:[a-f0-9]{64}$')
        self.assertEqual(json.loads(first.source_ref), ['fixture', 'sales', 'orders'])
        self.assertTrue(client.closed)
        for sql, _, settings in client.calls:
            self.assertEqual(settings['readonly'], 1)
        self.assertNotIn('Pesanan', repr(first))

    def test_hash_changes_for_each_fact_and_source(self):
        original = self.collector(Client()).capture('sales', 'orders').source_version
        for index, value in ((2, 'MergeTree'), (3, 'Lain'), (4, 'reg'), (5, 'Nullable(String)'), (6, 'Lain')):
            rows = [list(r) for r in records()]
            for row in rows:
                if index in (2, 3) or row[7] == 1:
                    row[index] = value
            self.assertNotEqual(self.collector(Client(rows)).capture('sales', 'orders').source_version, original)
        self.assertNotEqual(self.collector(Client(), source_id='other').capture('sales', 'orders').source_version, original)
        rows = [list(r) for r in records()]
        rows[0][7], rows[1][7] = 2, 1
        self.assertNotEqual(self.collector(Client(rows)).capture('sales', 'orders').source_version, original)

    def test_native_driver_list_rows_are_accepted(self):
        client = Client(); client.row_lists = True
        self.assertEqual(self.collector(client).capture('sales', 'orders').verified_metadata['table'], 'orders')

    def test_scope_and_system_database_denied_before_connection(self):
        calls = []
        collector = self.module.SchemaCollector(lambda: calls.append(1), source_id='fixture', tables=[('sales', 'orders')])
        for db, table in (('sales', 'secret'), ('system', 'users'), ('sales', ''), ('sales', 'x\x00')):
            with self.subTest(db=db, table=table), self.assertRaises(self.module.SnapshotError):
                collector.capture(db, table)
        self.assertEqual(calls, [])
        for tables in ([], [('system', 'tables')], [('information_schema', 'tables')], [('sales',)], [('sales', 'x'*257)]):
            with self.assertRaises(ValueError): self.collector(Client(), tables=tables)

    def test_exact_names_bound_and_quoted_without_like_enumeration(self):
        db, table = 'data`base', "order_%\\`'; DROP TABLE x;--"
        client = Client(records(table, db))
        self.collector(client, tables=[(db, table)]).capture(db, table)
        reads = [call for call in client.calls if call[0].startswith('SELECT t.database')]
        self.assertEqual(len(reads), 2)
        for sql, parameters, _ in reads:
            self.assertEqual(parameters, {'database': db, 'table': table})
            self.assertNotIn(table, sql)
            self.assertNotIn('LIKE', sql)
        grants = [call[0] for call in client.calls if call[0].startswith('CHECK GRANT')]
        self.assertEqual(grants, ["CHECK GRANT SELECT ON `data\\`base`.`order_%\\\\\\`'; DROP TABLE x;--`"] * 2)

    def test_invalid_partial_or_oversized_metadata_never_returns_snapshot(self):
        cases = [[], records() + [records()[0]], records(database='other'), records(table='other')]
        for index, value in ((0, 'other'), (2, ''), (3, 'x'*65537), (4, ''), (5, None), (6, None), (7, 0), (7, True)):
            row = list(records()[0]); row[index] = value
            cases.append([tuple(row)])
        mixed = [list(r) for r in records()]; mixed[1][3] = 'different'; cases.append(mixed)
        gap = [list(r) for r in records()]; gap[1][7] = 3; cases.append(gap)
        cases.append([('sales', 'orders', 'Memory', '', f'c{i}', 'String', '', i+1) for i in range(4097)])
        cases.append([('sales', 'orders', 'Memory', '', f'c{i}', 'String', '雪'*10000, i+1) for i in range(40)])
        for rows in cases:
            client = Client(rows)
            with self.subTest(size=len(rows)), self.assertRaises(self.module.SnapshotError):
                self.collector(client).capture('sales', 'orders')
            self.assertTrue(client.closed)

    def test_revocation_or_schema_change_during_capture_discards_result(self):
        for grants in ([0], [1, 0]):
            client = Client(); client.grants = grants
            with self.assertRaises(self.module.SnapshotError): self.collector(client).capture('sales', 'orders')
            self.assertTrue(client.closed)
        client = Client(); changed = copy.deepcopy(records()); changed[1] = (*changed[1][:5], 'UInt64', *changed[1][6:])
        client.reads = [records(), changed]
        with self.assertRaises(self.module.SnapshotError): self.collector(client).capture('sales', 'orders')
        self.assertTrue(client.closed)

    def test_missing_effective_limits_fail_closed(self):
        for index, bad in ((0, 0), (0, 2), (1, 0), (1, 100), (2, 0), (3, 2000000), (4, 0), (5, 0), (6, 0), (7, 'break'), (8, 'break'), (9, 'break')):
            client = Client(); policy = list(client.policy[0]); policy[index] = bad; client.policy = [policy]
            with self.subTest(index=index, bad=bad), self.assertRaises(self.module.SnapshotError):
                self.collector(client).capture('sales', 'orders')
            self.assertEqual(len(client.calls), 1)
            self.assertTrue(client.closed)

    def test_cancellation_and_errors_close_connection_without_secret(self):
        client = Client()
        with self.assertRaises(self.module.SnapshotError):
            self.collector(client).capture('sales', 'orders', cancelled=lambda: True)
        self.assertEqual(client.calls, [])
        client = Client()
        def broken(*a, **kw): raise RuntimeError('secret-password-private-metadata')
        client.query = broken
        with self.assertRaises(self.module.SnapshotError) as caught:
            self.collector(client).capture('sales', 'orders')
        self.assertNotIn('secret', str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertTrue(client.closed)
        client = Client()
        ticks = iter([False, False, True])
        with self.assertRaises(self.module.SnapshotError):
            self.collector(client).capture('sales', 'orders', cancelled=lambda: next(ticks, True))
        self.assertTrue(client.closed)

    def test_production_factory_enforces_tls_and_does_not_accept_url_or_dsn(self):
        captured = []
        driver = SimpleNamespace(get_client=lambda **kwargs: captured.append(kwargs) or Client())
        with patch.dict(sys.modules, {'clickhouse_connect': driver}):
            factory = self.module.production_client_factory(host='warehouse.example', port=8443,
                                                            username='ingest', password='private-password')
            self.assertEqual(captured, [])
            first, second = factory(), factory()
        self.assertIsNot(first, second)
        self.assertTrue(captured[0]['secure']); self.assertTrue(captured[0]['verify'])
        self.assertEqual(captured[0]['settings']['readonly'], 1)
        self.assertEqual(captured[0]['query_retries'], 0)
        self.assertLessEqual(captured[0]['connect_timeout'], 5)
        self.assertLessEqual(captured[0]['send_receive_timeout'], 15)
        for host in ('', 'http://example.com', 'user:pass@host', 'host/path', 'a,b', 'host?x=y'):
            with self.assertRaises(ValueError):
                self.module.production_client_factory(host=host, port=8443, username='ingest', password='private-password')


if __name__ == '__main__': unittest.main()
