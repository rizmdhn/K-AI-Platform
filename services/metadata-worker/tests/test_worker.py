import importlib.util
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class WorkerTests(unittest.TestCase):
    def test_queue_module_exists(self):
        self.assertTrue((ROOT / 'job_queue.py').exists())

    def test_lease_policy_rejects_unbounded_values(self):
        if not (ROOT / 'job_queue.py').exists():
            self.fail('Queue policy is not implemented')
        from job_queue import JobQueue
        for kwargs in ({'lease_seconds': 0}, {'max_attempts': 0}, {'max_attempts': 101}, {'backoff_seconds': -1}, {'backoff_cap': 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                JobQueue(lambda: None, **kwargs)

    def test_loop_uses_explicit_handlers_and_records_safe_failures(self):
        if not (ROOT / 'worker.py').exists():
            self.fail('Worker loop is not implemented')
        from job_queue import Job
        from worker import Worker
        from uuid import uuid4
        class Store:
            lease_seconds = 3
            def __init__(self): self.failures = []; self.finished = []
            def claim(self, source_types): return Job(uuid4(), 'document', 'fixture', 'v1', 1)
            def heartbeat(self, job): return True
            def fail(self, job, code): self.failures.append(code); return True
            def finish(self, job, persist=None): self.finished.append(job); return True
        store = Store()
        def broken(job, cancelled): raise RuntimeError('secret must not be stored')
        worker = Worker(store, {'document': broken})
        self.assertEqual(worker.run_once(), 'failed')
        self.assertEqual(store.failures, ['HANDLER_FAILED'])
        self.assertFalse(store.finished)
        with self.assertRaises(ValueError): Worker(store, {})
        with self.assertRaises(ValueError): Worker(store, {'arbitrary': broken})

    def test_lost_heartbeat_discards_handler_output(self):
        from job_queue import Job
        from worker import Worker
        from uuid import uuid4
        class Store:
            lease_seconds = 0.15
            def claim(self, source_types): return Job(uuid4(), 'document', 'fixture', 'v1', 1)
            def heartbeat(self, job): return False
            def finish(self, job, persist=None): raise AssertionError('Lost lease committed')
            def fail(self, job, code): raise AssertionError('Lost lease changed job')
        def handler(job, cancelled):
            for _ in range(100):
                if cancelled(): return None
                threading.Event().wait(.01)
            raise AssertionError('Cancellation signal was not delivered')
        self.assertEqual(Worker(Store(), {'document': handler}).run_once(), 'lease_lost')

    def test_shutdown_never_commits_handler_results(self):
        if not (ROOT / 'worker.py').exists():
            self.fail('Worker loop is not implemented')
        from job_queue import Job
        from worker import Worker
        from uuid import uuid4
        stop = threading.Event()
        class Store:
            lease_seconds = 3
            def __init__(self): self.failures = []
            def claim(self, source_types): return Job(uuid4(), 'document', 'fixture', 'v1', 1)
            def heartbeat(self, job): return True
            def fail(self, job, code): self.failures.append(code); return True
            def finish(self, job, persist=None): raise AssertionError('Cancelled handler committed')
        store = Store()
        def handler(job, cancelled): stop.set(); return None
        worker = Worker(store, {'document': handler}, stop=stop)
        self.assertEqual(worker.run_once(), 'cancelled')
        self.assertEqual(store.failures, ['CANCELLED'])


if __name__ == '__main__': unittest.main()
