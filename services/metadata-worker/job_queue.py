"""Internal PostgreSQL queue only; handlers, enrollment, extraction and ACL search are separate."""

from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4
import os


class QueueUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Metadata queue unavailable.")


class LeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class Job:
    job_id: UUID
    source_type: str
    source_ref: str
    source_version: str
    attempt: int


class JobQueue:
    def __init__(
        self,
        connect,
        *,
        lease_seconds=30,
        max_attempts=3,
        backoff_seconds=5,
        backoff_cap=300
    ):
        for value, lower, upper in [
            (lease_seconds, 1, 3600),
            (max_attempts, 1, 100),
            (backoff_seconds, 1, 3600),
            (backoff_cap, 1, 86400),
        ]:
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError("Queue policy is outside supported bounds.")
        if backoff_cap < backoff_seconds:
            raise ValueError("Backoff cap must cover the initial backoff.")
        self.connect, self.lease_seconds, self.max_attempts = (
            connect,
            lease_seconds,
            max_attempts,
        )
        self.backoff_seconds, self.backoff_cap = backoff_seconds, backoff_cap

    @classmethod
    def from_dsn(cls, dsn, **policy):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict

        try:
            info = conninfo_to_dict(dsn)
            host = info.get("host", "")
            if (
                not host
                or "," in host
                or info.get("hostaddr")
                or info.get("service")
                or os.environ.get("PGHOSTADDR")
                or os.environ.get("PGSERVICE")
            ):
                raise ValueError()
            if not host.startswith("/") and info.get("sslmode") != "verify-full":
                raise ValueError()
        except Exception:
            raise ValueError(
                "Use an explicit Unix socket or verified PostgreSQL TLS."
            ) from None
        return cls(
            lambda: psycopg.connect(
                dsn, connect_timeout=3, application_name="kai-metadata-worker"
            ),
            **policy
        )

    @contextmanager
    def transaction(self):
        try:
            with self.connect() as conn:
                # A fresh idle connection is required; never inherit another transaction/savepoint.
                if conn.info.transaction_status != 0:
                    raise QueueUnavailable()
                with conn.transaction():
                    conn.execute("SET LOCAL lock_timeout='1000ms'")
                    conn.execute("SET LOCAL statement_timeout='2000ms'")
                    conn.execute(
                        "SET LOCAL idle_in_transaction_session_timeout='3000ms'"
                    )
                    with conn.cursor() as cursor:
                        yield cursor
        except LeaseLost:
            raise
        except Exception:
            raise QueueUnavailable() from None

    def enqueue(self, source_type, source_ref, source_version):
        # Caller must be the separately authorized producer; the worker role has no INSERT grant.
        if source_type not in ("clickhouse", "document") or any(
            not isinstance(v, str)
            or not v.strip()
            or "\x00" in v
            or len(v.encode()) > 4096
            for v in (source_ref, source_version)
        ):
            raise ValueError("Invalid job source identity.")
        with self.transaction() as cursor:
            row = cursor.execute(
                """INSERT INTO kai.ingestion_jobs(job_id,source_type,source_ref,source_version,status)
                VALUES(%s,%s,%s,%s,'queued') ON CONFLICT(source_type,source_ref,source_version) DO NOTHING RETURNING job_id""",
                (uuid4(), source_type, source_ref, source_version),
            ).fetchone()
            if row:
                return row[0]
            row = cursor.execute(
                "SELECT job_id FROM kai.ingestion_jobs WHERE source_type=%s AND source_ref=%s AND source_version=%s",
                (source_type, source_ref, source_version),
            ).fetchone()
            if not row:
                raise QueueUnavailable()
            return row[0]

    def claim(self, source_types=("clickhouse", "document")):
        if not source_types or any(
            item not in ("clickhouse", "document") for item in source_types
        ):
            raise ValueError("Unsupported handler source types.")
        with self.transaction() as cursor:
            # Bound cleanup work so exhausted leases cannot monopolize a transaction.
            for _ in range(64):
                row = cursor.execute(
                    """SELECT job_id,source_type,source_ref,source_version,attempts
                    FROM kai.ingestion_jobs WHERE source_type=ANY(%s)
                    AND ((status IN ('queued','failed') AND available_at<=clock_timestamp())
                         OR (status='running' AND (locked_until IS NULL OR locked_until<=clock_timestamp())))
                    ORDER BY available_at,job_id FOR UPDATE SKIP LOCKED LIMIT 1""",
                    (list(source_types),),
                ).fetchone()
                if not row:
                    return None
                if row[4] >= self.max_attempts:
                    cursor.execute(
                        """UPDATE kai.ingestion_jobs SET status='dead_letter',locked_until=NULL,
                        last_error_code='MAX_ATTEMPTS',updated_at=clock_timestamp() WHERE job_id=%s""",
                        (row[0],),
                    )
                    continue
                cursor.execute(
                    """UPDATE kai.ingestion_jobs SET status='running',attempts=attempts+1,
                    locked_until=clock_timestamp()+(%s*interval '1 second'),last_error_code=NULL,
                    updated_at=clock_timestamp() WHERE job_id=%s""",
                    (self.lease_seconds, row[0]),
                )
                return Job(row[0], row[1], row[2], row[3], row[4] + 1)
            return None

    def active(self, cursor, job):
        row = cursor.execute(
            "SELECT status,attempts FROM kai.ingestion_jobs WHERE job_id=%s FOR UPDATE",
            (job.job_id,),
        ).fetchone()
        if not row or row != ("running", job.attempt):
            return False
        # Re-evaluate wall-clock expiry after lock acquisition, not in the pre-lock predicate.
        return (
            cursor.execute(
                "SELECT locked_until>clock_timestamp() FROM kai.ingestion_jobs WHERE job_id=%s",
                (job.job_id,),
            ).fetchone()[0]
            is True
        )

    def heartbeat(self, job):
        with self.transaction() as cursor:
            if not self.active(cursor, job):
                return False
            cursor.execute(
                """UPDATE kai.ingestion_jobs SET locked_until=clock_timestamp()+(%s*interval '1 second'),
                updated_at=clock_timestamp() WHERE job_id=%s AND locked_until>clock_timestamp() RETURNING job_id""",
                (self.lease_seconds, job.job_id),
            )
            return cursor.fetchone() is not None

    def finish(self, job, persist=None):
        try:
            with self.transaction() as cursor:
                if not self.active(cursor, job):
                    return False
                if persist is not None:
                    persist(cursor, job)
                row = cursor.execute(
                    """UPDATE kai.ingestion_jobs SET status='completed',locked_until=NULL,
                    last_error_code=NULL,updated_at=clock_timestamp() WHERE job_id=%s
                    AND status='running' AND attempts=%s AND locked_until>clock_timestamp() RETURNING job_id""",
                    (job.job_id, job.attempt),
                ).fetchone()
                if not row:
                    raise LeaseLost()
                return True
        except LeaseLost:
            return False

    def fail(self, job, code):
        if code not in ("HANDLER_FAILED", "COMMIT_FAILED", "CANCELLED"):
            raise ValueError("Unsupported failure code.")
        delay = min(
            self.backoff_cap,
            self.backoff_seconds * 2 ** min(max(job.attempt - 1, 0), 32),
        )
        with self.transaction() as cursor:
            if not self.active(cursor, job):
                return False
            cursor.execute(
                """UPDATE kai.ingestion_jobs SET status=%s,available_at=clock_timestamp()+(%s*interval '1 second'),
                locked_until=NULL,last_error_code=%s,updated_at=clock_timestamp() WHERE job_id=%s AND locked_until>clock_timestamp() RETURNING job_id""",
                (
                    "dead_letter" if job.attempt >= self.max_attempts else "failed",
                    delay,
                    code,
                    job.job_id,
                ),
            )
            return cursor.fetchone() is not None
