"""Runs only in the network-disabled disposable database fixture."""
import os
from pathlib import Path
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from uuid import uuid4

import psycopg
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from job_queue import JobQueue, QueueUnavailable

DB = os.environ['KAI_WORKER_TEST_DB']

def connection(user):
    return psycopg.connect(host='/pgsocket', dbname=DB, user='kai_worker_owner' if user=='kai_migrator' else user, connect_timeout=3)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.queue = JobQueue(lambda: connection('kai_worker_probe'), lease_seconds=3, max_attempts=3, backoff_seconds=2, backoff_cap=10)
        self.owner = JobQueue(lambda: connection('kai_migrator'), lease_seconds=3)
        with connection('kai_migrator') as conn:
            conn.execute('TRUNCATE kai.ingestion_jobs,kai.worker_fixture_results')

    def enqueue(self, version='v1'):
        return self.owner.enqueue('document', 'fixture/document', version)

    def expire(self, job_id):
        with connection('kai_migrator') as conn:
            conn.execute("UPDATE kai.ingestion_jobs SET locked_until=clock_timestamp()-interval '1 second',available_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (job_id,))

    def state(self, job_id):
        with connection('kai_migrator') as conn:
            return conn.execute('SELECT status,attempts,last_error_code FROM kai.ingestion_jobs WHERE job_id=%s',(job_id,)).fetchone()

    def test_idempotent_enqueue_and_concurrent_claim(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: self.enqueue(), range(2)))
        self.assertEqual(ids[0], ids[1])
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = list(pool.map(lambda _: self.queue.claim(), range(2)))
        self.assertEqual(sum(job is not None for job in jobs),1)
        job = next(job for job in jobs if job)
        self.assertEqual(job.attempt,1)
        self.assertTrue(self.queue.finish(job))
        self.assertEqual(self.enqueue(),job.job_id)
        self.assertIsNone(self.queue.claim())

    def test_skip_locked_leaves_other_jobs_available(self):
        first = self.enqueue('v1')
        second = self.enqueue('v2')
        with connection('kai_migrator') as conn:
            conn.execute('SELECT job_id FROM kai.ingestion_jobs WHERE job_id=%s FOR UPDATE',(first,))
            claimed = self.queue.claim()
            self.assertEqual(claimed.job_id,second)

    def test_heartbeat_expiry_and_old_attempt_fencing(self):
        job_id = self.enqueue()
        old = self.queue.claim()
        self.assertTrue(self.queue.heartbeat(old))
        self.expire(job_id)
        self.assertFalse(self.queue.heartbeat(old))
        current = self.queue.claim()
        self.assertEqual(current.attempt,2)
        called = []
        self.assertFalse(self.queue.finish(old,lambda cursor,job:called.append(True)))
        self.assertFalse(self.queue.fail(old,'HANDLER_FAILED'))
        self.assertFalse(called)
        self.assertTrue(self.queue.finish(current))
        self.assertEqual(self.state(job_id)[0],'completed')

    def test_retry_backoff_and_dead_letter(self):
        job_id = self.enqueue()
        for attempt in range(1,4):
            job = self.queue.claim()
            self.assertEqual(job.attempt,attempt)
            self.assertTrue(self.queue.fail(job,'HANDLER_FAILED'))
            if attempt<3:
                self.assertIsNone(self.queue.claim())
                with connection('kai_migrator') as conn:
                    delay=conn.execute('SELECT extract(epoch FROM available_at-clock_timestamp()) FROM kai.ingestion_jobs WHERE job_id=%s',(job_id,)).fetchone()[0]
                self.assertGreater(delay,0)
                self.assertLessEqual(delay,10)
                self.expire(job_id)
        self.assertEqual(self.state(job_id),('dead_letter',3,'HANDLER_FAILED'))
        self.assertIsNone(self.queue.claim())

    def test_exhausted_expired_attempt_is_dead_lettered(self):
        job_id = self.enqueue()
        with connection('kai_migrator') as conn:
            conn.execute("UPDATE kai.ingestion_jobs SET status='running',attempts=3,locked_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",(job_id,))
        self.assertIsNone(self.queue.claim())
        self.assertEqual(self.state(job_id)[0],'dead_letter')

    def test_callback_and_completion_commit_or_rollback_together(self):
        job_id = self.enqueue()
        job = self.queue.claim()
        def broken(cursor, item):
            cursor.execute('INSERT INTO kai.worker_fixture_results VALUES(%s,%s)',(item.job_id,'fixture'))
            raise RuntimeError('secret exception must not persist')
        with self.assertRaises(QueueUnavailable) as error:
            self.queue.finish(job,broken)
        self.assertNotIn('secret exception',str(error.exception))
        with connection('kai_migrator') as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM kai.worker_fixture_results').fetchone()[0],0)
        self.assertEqual(self.state(job_id)[0],'running')
        self.assertTrue(self.queue.finish(job,lambda cursor,item:cursor.execute('INSERT INTO kai.worker_fixture_results VALUES(%s,%s)',(item.job_id,'fixture'))))
        with connection('kai_migrator') as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM kai.worker_fixture_results').fetchone()[0],1)

    def test_post_callback_expiry_rolls_back_results(self):
        job_id = self.enqueue()
        job = self.queue.claim()
        def expire_during_result(cursor,item):
            cursor.execute('INSERT INTO kai.worker_fixture_results VALUES(%s,%s)',(item.job_id,'must rollback'))
            cursor.execute("UPDATE kai.ingestion_jobs SET locked_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",(item.job_id,))
        self.assertFalse(self.queue.finish(job,expire_during_result))
        with connection('kai_migrator') as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM kai.worker_fixture_results').fetchone()[0],0)
        self.assertEqual(self.state(job_id)[0],'running')

    def test_expiry_rechecked_after_waiting_for_row_lock(self):
        job_id = self.enqueue()
        job = self.queue.claim()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with connection('kai_migrator') as conn:
                conn.execute('SELECT job_id FROM kai.ingestion_jobs WHERE job_id=%s FOR UPDATE',(job_id,))
                future=pool.submit(self.queue.finish,job)
                deadline=time.monotonic()+0.7
                while time.monotonic()<deadline:
                    with connection('postgres') as monitor:
                        waiting=monitor.execute("SELECT count(*) FROM pg_stat_activity WHERE usename='kai_worker_probe' AND wait_event_type='Lock'").fetchone()[0]
                    if waiting:break
                    time.sleep(.01)
                else:self.fail('Worker did not wait on row lock')
                conn.execute("UPDATE kai.ingestion_jobs SET locked_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",(job_id,))
            self.assertFalse(future.result(timeout=3))

    def test_dsn_factory_rejects_unverified_remote_connections(self):
        with self.assertRaises(ValueError) as error:
            JobQueue.from_dsn('host=remote.example.invalid password=fixture-secret sslmode=disable')
        self.assertNotIn('fixture-secret', str(error.exception))
        for dsn in ['host=/missing/socket,remote.example.invalid sslmode=disable', 'host=/pgsocket hostaddr=127.0.0.1 sslmode=disable', 'host=/pgsocket service=opaque']:
            with self.subTest(dsn=dsn), self.assertRaises(ValueError):
                JobQueue.from_dsn(dsn)
        from unittest.mock import patch
        with patch.dict(os.environ, {'PGHOSTADDR': '127.0.0.1'}), self.assertRaises(ValueError):
            JobQueue.from_dsn('host=/pgsocket')
        queue = JobQueue.from_dsn('host=/pgsocket dbname='+DB+' user=kai_worker_probe')
        self.enqueue()
        self.assertIsNotNone(queue.claim())

    def test_runtime_cannot_enqueue_change_source_or_read_catalog(self):
        self.enqueue()
        with self.assertRaises(QueueUnavailable): self.queue.enqueue('document','unauthorized','v1')
        for sql in ['SELECT * FROM kai.catalog_assets','UPDATE kai.ingestion_jobs SET source_ref=\'other\'','DELETE FROM kai.ingestion_jobs','CREATE TABLE kai.forbidden(value text)']:
            with connection('kai_worker_probe') as conn:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):conn.execute(sql)
                conn.rollback()


suite=unittest.defaultTestLoader.loadTestsFromTestCase(QueueTests)
result=unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():sys.exit(1)
print('Queue PostgreSQL integration passed: claims, locks, expiry, fencing, retries, atomic results and least privilege.')
