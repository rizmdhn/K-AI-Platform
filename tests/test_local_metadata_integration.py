"""Opt-in disposable Docker proof: KAI_LOCAL_METADATA_INTEGRATION=1 python3 -m unittest discover -s tests -p test_local_metadata_integration.py -v.

Uses unique containers/volumes, no published ports, tmpfs PostgreSQL data, and no root .env access.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import setup_local_metadata as setup


@unittest.skipUnless(os.getenv('KAI_LOCAL_METADATA_INTEGRATION') == '1', 'Set KAI_LOCAL_METADATA_INTEGRATION=1 for disposable Docker proof')
class LocalMetadataIntegrationTests(unittest.TestCase):
    def test_scram_runtime_driver_keyring_and_repeated_provision(self):
        name = 'kai-local-metadata-test-' + secrets.token_hex(6)
        socket_volume, key_volume = name + '-socket', name + '-keys'
        fields = {'KAI_MCP_ENABLED': 'true', 'CLICKHOUSE_HOST': 'fixture.invalid'}
        fields.update(setup.configuration_updates(fields))
        image_line = next(line for line in (ROOT / 'deploy/docker-compose.metadata-local.yml').read_text().splitlines() if 'image: postgres:' in line)
        image = image_line.split('image:', 1)[1].strip()
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            env = stage / '.env'
            env.write_text('KAI_METADATA_ADMIN_PASSWORD=' + fields['KAI_METADATA_ADMIN_PASSWORD'] + '\n')
            env.chmod(0o600)
            config = stage / 'compose.json'
            config.write_text(json.dumps({'services': {'kai-metadata': {
                'image': image, 'network_mode': 'none',
                'environment': {'POSTGRES_DB': 'kai_metadata', 'POSTGRES_USER': 'postgres',
                    'POSTGRES_PASSWORD': '${KAI_METADATA_ADMIN_PASSWORD}',
                    'POSTGRES_INITDB_ARGS': '--auth-local=scram-sha-256 --auth-host=scram-sha-256'},
                'tmpfs': ['/var/lib/postgresql/data'],
                'volumes': [socket_volume + ':/var/run/postgresql', str(ROOT / 'db') + ':/migrations:ro'],
                'healthcheck': {'test': ['CMD-SHELL', 'test "$$(cat /proc/1/comm)" = postgres && pg_isready -h /var/run/postgresql -U postgres -d kai_metadata'],
                                'interval': '1s', 'timeout': '3s', 'retries': 90}}},
                'volumes': {socket_volume: {'name': socket_volume}}}))
            helper = setup.LocalMetadata(ROOT)
            helper.compose = ['docker', 'compose', '--project-name', name, '--env-file', str(env), '-f', str(config)]
            real_run = helper.run
            def fixture_run(command, input=None):
                return real_run([part.replace('kai_metadata_binding_keys', key_volume) for part in command], input=input)
            helper.run = fixture_run
            try:
                for attempt in range(2):
                    helper.provision(fields)
                    scram = helper.run(helper.psql('-At'), input="SELECT count(*) FROM pg_authid WHERE rolname IN ('kai_binding_runtime','kai_enrollment_runtime') AND rolcanlogin AND rolpassword LIKE 'SCRAM-SHA-256$%';\n")
                    self.assertEqual(scram, '2')
                    # Use the deployed driver's exact package and uid against the read-only Unix socket.
                    dsns = [setup.bootstrap.value(fields[key]) for key in setup.RUNTIME_ROLES]
                    driver = "import psycopg\nfrom credential_crypto import load_keyring\nassert len(load_keyring('/run/kai-binding-keys')[1]) == 32\n"
                    driver += 'dsns=' + repr(dsns) + '\n'
                    driver += "for dsn in dsns:\n with psycopg.connect(dsn) as connection:\n  assert connection.execute('select current_database()').fetchone()[0] == 'kai_metadata'\n try:\n  psycopg.connect(dsn.rsplit('=',1)[0]+'=wrong-password')\n except psycopg.OperationalError:\n  pass\n else:\n  raise AssertionError('Wrong password accepted')\n"
                    helper.run(['docker', 'run', '--rm', '--network', 'none', '--pull', 'never', '--user', '10001:10001',
                        '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '-i',
                        '--mount', 'type=volume,source=' + socket_volume + ',target=/run/kai-metadata,readonly',
                        '--mount', 'type=volume,source=' + key_volume + ',target=/run/kai-binding-keys,readonly',
                        '--entrypoint', 'python', 'kai-clickhouse-mcp:0.7.0', '-'], input=driver)
                    stamp = helper.run(['docker', 'run', '--rm', '--network', 'none', '--pull', 'never', '--user', '10001:10001',
                        '--read-only', '--cap-drop', 'ALL', '--mount', 'type=volume,source=' + key_volume + ',target=/keys,readonly',
                        '--entrypoint', 'python', 'kai-clickhouse-mcp:0.7.0', '-c',
                        "import os; s=os.stat('/keys/1.key'); print(s.st_mtime_ns,s.st_ino,s.st_size)"])
                    if attempt:
                        self.assertEqual(stamp, original_stamp)
                    original_stamp = stamp
            finally:
                # Explicit, unique fixture resources only, even after a failed proof.
                subprocess.run(helper.compose + ['down', '--volumes', '--remove-orphans'], cwd=ROOT,
                    env=helper.environment, capture_output=True, text=True, timeout=60)
                subprocess.run(['docker', 'volume', 'rm', key_volume], cwd=ROOT,
                    env=helper.environment, capture_output=True, text=True, timeout=60)
