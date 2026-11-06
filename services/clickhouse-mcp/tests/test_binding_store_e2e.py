"""Opt-in real PG+crypto lookup test; both containers have networking disabled."""
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


@unittest.skipUnless(IMAGE, 'set KAI_BINDING_E2E_IMAGE for isolated PostgreSQL/crypto integration')
class ManagedBindingEndToEnd(LocalPostgresTestCase):
    SHARE_SOCKET = True

    def test_real_view_driver_crypto_isolation_revocation_and_timeout(self):
        self.sql('\\ir /migrations/migrate_identity.sql\n\\ir /migrations/migrate_approvals.sql\n\\ir /migrations/migrate_enrollment.sql\n\\ir /migrations/migrate_admin_warehouse.sql\n')
        self.execute('\\ir /migrations/create_binding_roles.sql\n', self.database)
        self.sql('\\ir /migrations/grant_mcp_bindings.sql\n')
        self.execute('CREATE ROLE kai_lookup_probe LOGIN IN ROLE kai_mcp;')
        script = r'''
import asyncio, os, tempfile, time
from pathlib import Path
from uuid import uuid4
import psycopg
import binding_credentials
from binding_credentials import get_managed_credentials
from credential_crypto import BindingContext, encrypt_password, rotate_password
from tool_errors import GatewayError

async def main():
    admin = psycopg.connect(os.environ['KAI_TEST_ADMIN_DSN'], autocommit=True)
    async def verify_native(uid, username, service):
        assert (uid,username) in [('a'*24,'personal_alice'),('b'*24,'personal_bob')]
        assert service == 'https://staging.example:8443'
    binding_credentials.verify_native_binding = verify_native
    keys = {1: os.urandom(32), 2: os.urandom(32)}
    with tempfile.TemporaryDirectory() as directory:
        for version, key in keys.items():
            path = Path(directory) / f'{version}.key'
            path.write_bytes(key)
            path.chmod(0o600)
        os.environ['KAI_BINDING_KEY_DIR'] = directory
        contexts = []
        for uid, username, password in (('a'*24,'personal_alice','fixture-alice-secret'), ('b'*24,'personal_bob','fixture-bob-secret')):
            context = BindingContext(uuid4(), username, 1, uuid4(),'https://staging.example:8443')
            contexts.append(context)
            admin.execute('INSERT INTO kai.ai_principals(principal_id,issuer,subject,librechat_user_id) VALUES(%s,%s,%s,%s)',
                          (context.principal_id,'librechat-local',uid,uid))
            admin.execute('INSERT INTO kai.approved_ch_accounts(principal_id,ch_username) VALUES(%s,%s)',
                          (context.principal_id,username))
            admin.execute("INSERT INTO kai.ch_bindings(binding_id,principal_id,ch_username,cipher_blob,key_version,status,verified_at,service_target) VALUES(%s,%s,%s,%s,1,'active',now(),'https://staging.example:8443')",
                          (context.binding_id,context.principal_id,username,encrypt_password(password,context,keys)))
        credentials = await asyncio.gather(get_managed_credentials('a'*24),get_managed_credentials('b'*24))
        assert credentials == [('personal_alice','fixture-alice-secret'),('personal_bob','fixture-bob-secret')]
        async def denied(uid, code):
            try:
                await get_managed_credentials(uid)
            except GatewayError as error:
                assert error.code == code
                assert 'secret' not in str(error)
            else:
                raise AssertionError('ineligible lookup unexpectedly succeeded')
        await denied('c'*24,'AUTH_REQUIRED')
        admin.execute('UPDATE kai.approved_ch_accounts SET revoked_at=now() WHERE principal_id=%s',(contexts[0].principal_id,))
        await denied('a'*24,'AUTH_REQUIRED')
        assert await get_managed_credentials('b'*24) == credentials[1]
        admin.execute('UPDATE kai.approved_ch_accounts SET revoked_at=NULL WHERE principal_id=%s',(contexts[0].principal_id,))
        old = admin.execute('SELECT cipher_blob FROM kai.ch_bindings WHERE binding_id=%s',(contexts[0].binding_id,)).fetchone()[0]
        context, rotated = rotate_password(old, contexts[0], 2, keys)
        admin.execute('UPDATE kai.ch_bindings SET cipher_blob=%s,key_version=2,rotated_at=now() WHERE binding_id=%s',(rotated,context.binding_id))
        assert await get_managed_credentials('a'*24) == credentials[0]
        admin.execute('UPDATE kai.ch_bindings SET cipher_blob=%s WHERE binding_id=%s',(b'0'*40,context.binding_id))
        await denied('a'*24,'DEPENDENCY_UNAVAILABLE')
        admin.execute('BEGIN')
        try:
            admin.execute('LOCK TABLE kai.ch_bindings IN ACCESS EXCLUSIVE MODE')
            started = time.monotonic()
            await denied('b'*24,'DEPENDENCY_UNAVAILABLE')
            assert time.monotonic()-started < 6
        finally:
            admin.execute('ROLLBACK')
        for _ in range(100):
            if admin.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name='kai-binding-lookup'").fetchone()[0] == 0:
                break
            await asyncio.sleep(.01)
        else:
            raise AssertionError('lookup leaked a PostgreSQL connection')
    admin.close()
    print('Real view/psycopg/AES isolation, revocation, rotation, tamper, timeout and connection cleanup passed.')

asyncio.run(main())
'''
        result = subprocess.run([
            'docker', 'run', '--rm', '-i', '--network', 'none', '--read-only', '--tmpfs', '/tmp',
            '--mount', f'type=bind,src={Path(__file__).resolve().parents[1]},dst=/app,readonly',
            '--user', '10001:10001', '--mount', f'type=volume,src={self.socket_volume},dst=/pgsocket,readonly',
            '--env', f'KAI_TEST_ADMIN_DSN=host=/pgsocket dbname={self.database} user=postgres',
            '--env', f'KAI_BINDING_DSN=host=/pgsocket dbname={self.database} user=kai_lookup_probe',
            '--env','CLICKHOUSE_HOST=staging.example',
            IMAGE, 'python', '-c', script,
        ], check=False, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('connection cleanup passed', result.stdout)
