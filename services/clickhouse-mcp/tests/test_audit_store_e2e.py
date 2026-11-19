"""Opt-in: the audit table on a real PostgreSQL (migration 013 + catalog grants), networking disabled.

Covers what the Admin Panel relies on at scale: batched inserts, cursor pages that never repeat or skip a row,
counts over all matches, the 24 h summary, and retention deletes in bounded slices.
"""
import os
from pathlib import Path
import subprocess
import sys
import unittest

IMAGE = os.environ.get('KAI_BINDING_E2E_IMAGE')
if IMAGE:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'db/tests'))
    from support import LocalPostgresTestCase
else:
    LocalPostgresTestCase = unittest.TestCase

SCRIPT = r'''
import json, time
from datetime import datetime, timedelta, timezone
import psycopg
import audit_store

DSN = "host=/pgsocket dbname=DATABASE user=kai_audit_probe"
A, B = "a" * 24, "b" * 24
now = datetime.now(timezone.utc)
writer = audit_store.Writer(DSN, lambda: 90, start=False)
line = lambda user, outcome: json.dumps({"librechat_user": user, "tool": "execute_readonly_sql", "outcome": outcome,
                                         "ch_user": "ch_" + user[0], "rows": 1, "duration_ms": 5})
rows = [audit_store.to_row(line(A if n % 2 else B, "access_denied" if n % 10 == 0 else "ok"), time.time() - 250 + n)
        for n in range(250)]
rows.append(audit_store.to_row(line(A, "unsafe_sql"), time.time() - 100 * 86400))  # past retention
writer.last_retention = time.monotonic()  # insert first, age out below
assert writer.flush_rows(rows)

seen, cursor, pages = [], "", 0
while True:
    page = audit_store.read_log(DSN, now, 90, cursor=cursor)
    seen += [r["ts"] + r["user"] for r in page["rows"]]
    pages += 1
    cursor = page["next_cursor"]
    if not cursor:
        break
assert pages == 3 and len(seen) == 250 and page["total"] == 250, (pages, len(seen), page["total"])
assert [r["ts"] for r in audit_store.read_log(DSN, now, 90)["rows"]][:2] == sorted(
    [r["ts"] for r in audit_store.read_log(DSN, now, 90)["rows"]][:2], reverse=True)
denied = audit_store.read_log(DSN, now, 1, user=B, outcome="access_denied")
assert denied["total"] == 25 and all(r["user"] == B for r in denied["rows"]), denied["total"]
assert audit_store.read_log(DSN, now, 1)["counts"] == {"ok": 225, "access_denied": 25}
assert sorted(audit_store.read_log(DSN, now, 1)["users"]) == [A, B]

summary = audit_store.summary(DSN, now)
assert summary["calls_24h"] == 250 and summary["users"][B]["denied"] == 25, summary
assert summary["users"][B]["last_error"]["outcome"] == "access_denied" and summary["users"][A]["last_error"] is None

with psycopg.connect(DSN, autocommit=True) as c:
    assert c.execute("SELECT count(*) FROM kai.audit_events").fetchone()[0] == 251
audit_store.RETENTION_CHUNK = 1  # force the sliced loop
writer.last_retention = 0
writer.retain()
with psycopg.connect(DSN, autocommit=True) as c:
    assert c.execute("SELECT count(*) FROM kai.audit_events").fetchone()[0] == 250
    try:
        c.execute("UPDATE kai.audit_events SET outcome = 'ok'")
        raise SystemExit("catalog role must not edit audit rows")
    except psycopg.errors.InsufficientPrivilege:
        pass
print("audit store passed")
'''


@unittest.skipUnless(IMAGE, 'set KAI_BINDING_E2E_IMAGE for isolated PostgreSQL integration')
class AuditStoreEndToEnd(LocalPostgresTestCase):
    SHARE_SOCKET = True

    def test_pages_counts_summary_and_retention(self):
        self.execute('\\ir /migrations/create_catalog_role.sql\n')
        self.sql('\\ir /migrations/migrate_identity.sql\n\\ir /migrations/migrate_approvals.sql\n'
                 '\\ir /migrations/migrate_enrollment.sql\n\\ir /migrations/migrate_admin_warehouse.sql\n'
                 '\\ir /migrations/migrate_revocation_fence.sql\n\\ir /migrations/migrate.sql\n'
                 '\\ir /migrations/grant_catalog_access.sql\n')
        self.execute('CREATE ROLE kai_audit_probe LOGIN IN ROLE kai_catalog;')
        self.addCleanup(self.execute, f'DROP DATABASE {self.database} WITH (FORCE); '
                                      'DROP ROLE IF EXISTS kai_audit_probe, kai_catalog_runtime, kai_catalog;')
        result = subprocess.run([
            'docker', 'run', '--rm', '-i', '--network', 'none', '--read-only', '--tmpfs', '/tmp',
            '--mount', f'type=bind,src={Path(__file__).resolve().parents[1]},dst=/app,readonly',
            '--user', '10001:10001', '--mount', f'type=volume,src={self.socket_volume},dst=/pgsocket,readonly',
            IMAGE, 'python', '-c', SCRIPT.replace("DATABASE", self.database),
        ], check=False, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('audit store passed', result.stdout)


if __name__ == "__main__":
    unittest.main()
