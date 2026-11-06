"""Opt-in real queue transactions on disposable PostgreSQL; no app/CH/model access."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

IMAGE = os.environ.get('KAI_WORKER_TEST_IMAGE')
if IMAGE:
    from support import LocalPostgresTestCase
else:
    LocalPostgresTestCase = unittest.TestCase


@unittest.skipUnless(IMAGE, 'set KAI_WORKER_TEST_IMAGE to an existing local image with psycopg')
class WorkerQueueIntegration(LocalPostgresTestCase):
    SHARE_SOCKET = True

    def test_existing_source_column_grant_is_rejected(self):
        self.sql("CREATE SCHEMA kai AUTHORIZATION kai_migrator; CREATE TABLE kai.ai_principals (principal_id uuid PRIMARY KEY);\\ir /migrations/migrate.sql\n")
        self.execute('\\ir /migrations/create_worker_role.sql\n')
        self.sql('\\ir /migrations/grant_worker_access.sql\n')
        self.sql('GRANT UPDATE(source_ref) ON kai.ingestion_jobs TO kai_metadata_worker;')
        result = self.sql('\\ir /migrations/grant_worker_access.sql\n', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('excess grants', result.stderr)
        self.sql('REVOKE UPDATE(source_ref) ON kai.ingestion_jobs FROM kai_metadata_worker;')
        self.sql('\\ir /migrations/grant_worker_access.sql\n')

    def test_real_queue_transactions_and_runtime_privileges(self):
        self.sql("CREATE SCHEMA kai AUTHORIZATION kai_migrator; CREATE TABLE kai.ai_principals (principal_id uuid PRIMARY KEY);\\ir /migrations/migrate.sql\n")
        self.execute('\\ir /migrations/create_worker_role.sql\n')
        self.sql('\\ir /migrations/grant_worker_access.sql\n')
        self.execute('CREATE ROLE kai_worker_probe LOGIN IN ROLE kai_metadata_worker; CREATE ROLE kai_worker_owner LOGIN IN ROLE kai_migrator;')
        self.sql('CREATE TABLE kai.worker_fixture_results(job_id uuid PRIMARY KEY, value text);'
                 'GRANT SELECT,INSERT ON kai.worker_fixture_results TO kai_metadata_worker;')
        worker = Path(__file__).resolve().parents[2] / 'services/metadata-worker'
        result = subprocess.run(['docker','run','--rm','--network','none','--read-only','--tmpfs','/tmp',
                                 '--user','10001:10001','--mount',f'type=volume,src={self.socket_volume},dst=/pgsocket,readonly',
                                 '--mount',f'type=bind,src={worker},dst=/worker,readonly',
                                 '--env',f'KAI_WORKER_TEST_DB={self.database}', IMAGE,
                                 'python','/worker/tests/probe_postgres.py'],capture_output=True,text=True,timeout=70)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Queue PostgreSQL integration passed', result.stdout)
