"""Cooperative loop with explicitly supplied trusted handlers; no extractor is enabled here."""

import threading
from job_queue import QueueUnavailable


class Worker:
    def __init__(self, queue, handlers, *, stop=None, poll_seconds=0.5):
        if not handlers or any(
            key not in ("clickhouse", "document") or not callable(handler)
            for key, handler in handlers.items()
        ):
            raise ValueError("Explicit supported handlers are required.")
        if not 0.05 <= poll_seconds <= 60:
            raise ValueError("Invalid polling interval.")
        self.queue, self.handlers = queue, dict(handlers)
        self.stop = stop if stop is not None else threading.Event()
        self.poll_seconds = poll_seconds

    def run_once(self):
        if self.stop.is_set():
            return "cancelled"
        job = self.queue.claim(tuple(self.handlers))
        if job is None:
            return "idle"
        finished, lost = threading.Event(), threading.Event()

        def heartbeat():
            while not finished.wait(self.queue.lease_seconds / 3):
                try:
                    if not self.queue.heartbeat(job):
                        lost.set()
                        return
                except QueueUnavailable:
                    lost.set()
                    return

        thread = threading.Thread(
            target=heartbeat, name="metadata-lease-heartbeat", daemon=True
        )
        thread.start()
        code = None
        persist = None
        try:
            # Handlers compute outside the transaction and return a trusted SQL persistence callback.
            persist = self.handlers[job.source_type](
                job, lambda: self.stop.is_set() or lost.is_set()
            )
            if persist is not None and not callable(persist):
                code = "HANDLER_FAILED"
        except Exception:
            code = "HANDLER_FAILED"
        finally:
            finished.set()
            thread.join(timeout=6)
        if thread.is_alive() or lost.is_set():
            return "lease_lost"
        if self.stop.is_set():
            code = "CANCELLED"
        if code:
            return (
                ("cancelled" if code == "CANCELLED" else "failed")
                if self.queue.fail(job, code)
                else "lease_lost"
            )
        try:
            return "completed" if self.queue.finish(job, persist) else "lease_lost"
        except QueueUnavailable:
            return "failed" if self.queue.fail(job, "COMMIT_FAILED") else "lease_lost"

    def run(self):
        while not self.stop.is_set():
            try:
                state = self.run_once()
            except QueueUnavailable:
                state = "idle"
            if state in ("idle", "lease_lost"):
                self.stop.wait(self.poll_seconds)
