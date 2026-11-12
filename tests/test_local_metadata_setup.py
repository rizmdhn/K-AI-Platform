from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


class LocalMetadataSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import setup_local_metadata
        cls.setup = setup_local_metadata

    def fields(self):
        return {'KAI_MCP_ENABLED': 'true', 'CLICKHOUSE_HOST': 'warehouse.example',
                'KAI_BINDING_KEY_DIR': '/run/kai-binding-keys'}

    def test_creates_distinct_secrets_and_preserves_every_value_on_rerun(self):
        updates = self.setup.configuration_updates(self.fields())
        fields = {**self.fields(), **updates}
        passwords = [fields['KAI_METADATA_ADMIN_PASSWORD']]
        for name, role in self.setup.RUNTIME_ROLES.items():
            dsn = self.setup.bootstrap.value(fields[name])
            self.assertRegex(dsn, rf'^host=/run/kai-metadata dbname=kai_metadata user={role} password=[0-9a-f]{{64}}$')
            passwords.append(dsn.rsplit('=', 1)[1])
        self.assertEqual(len(set(passwords)), 4)
        self.assertEqual(self.setup.configuration_updates(fields), {})

    def test_legacy_approval_secret_is_ignored_and_preserved(self):
        fields = {**self.fields(), 'KAI_APPROVAL_DSN': 'legacy-secret-left-untouched'}
        updates = self.setup.configuration_updates(fields)
        self.assertNotIn('KAI_APPROVAL_DSN', updates)
        self.assertEqual(len(self.setup.RUNTIME_ROLES), 3)
        fields.update(updates)
        self.assertEqual(self.setup.configuration_updates(fields), {})

    def test_existing_install_without_catalog_dsn_gets_one(self):
        fields = {**self.fields(), **self.setup.configuration_updates(self.fields())}
        del fields['KAI_CATALOG_DSN']
        self.assertEqual(list(self.setup.configuration_updates(fields)), ['KAI_CATALOG_DSN'])

    def test_refuses_external_dsn_or_host_keyring_without_generating_secrets(self):
        for setting, raw in [('KAI_BINDING_DSN', 'postgresql://external/db'),
                             ('KAI_BINDING_KEY_SOURCE', '/private/keys'),
                             ('KAI_MCP_ENABLED', 'false'),
                             ('KAI_BINDING_ACTIVE_KEY_VERSION', '2'),
                             ('KAI_LOCAL_METADATA_ENABLED', 'invalid'),
                             ('KAI_METADATA_ADMIN_PASSWORD', 'unsafe')]:
            with self.subTest(setting=setting):
                with self.assertRaises(ValueError):
                    self.setup.configuration_updates({**self.fields(), setting: raw})

    def test_refuses_partial_local_state_and_duplicate_secrets(self):
        generated = self.setup.configuration_updates(self.fields())
        generated['KAI_BINDING_DSN'] = ''
        with self.assertRaises(ValueError):
            self.setup.configuration_updates({**self.fields(), **generated})
        generated = self.setup.configuration_updates(self.fields())
        generated['KAI_METADATA_ADMIN_PASSWORD'] = self.setup.bootstrap.value(generated['KAI_BINDING_DSN']).rsplit('=', 1)[1]
        with self.assertRaises(ValueError):
            self.setup.configuration_updates({**self.fields(), **generated})

    def test_keyring_initialization_preserves_key_and_rejects_unsafe_entries(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'keys'
            self.setup.initialize_keyring(root, os.getuid(), os.getgid())
            original = (root / '1.key').read_bytes()
            self.assertEqual(len(original), 32)
            self.setup.initialize_keyring(root, os.getuid(), os.getgid())
            self.assertEqual((root / '1.key').read_bytes(), original)
            (root / '1.key').chmod(0o644)
            with self.assertRaises(ValueError):
                self.setup.initialize_keyring(root, os.getuid(), os.getgid())

    def test_keyring_rejects_symlink_hardlink_and_unknown_file(self):
        import os
        for kind in ('symlink', 'hardlink', 'unknown'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'keys'
                self.setup.initialize_keyring(root, os.getuid(), os.getgid())
                if kind == 'symlink':
                    (root / '1.key').unlink()
                    (root / '1.key').symlink_to(Path(directory) / 'outside')
                elif kind == 'hardlink':
                    os.link(root / '1.key', Path(directory) / 'outside')
                else:
                    (root / 'unexpected').write_bytes(b'x')
                with self.assertRaises(ValueError):
                    self.setup.initialize_keyring(root, os.getuid(), os.getgid())

    def test_commands_keep_password_out_of_argv_and_restart_only_metadata(self):
        helper = self.setup.LocalMetadata(ROOT, {})
        self.assertEqual(helper.compose[-2:], ['-f', 'deploy/docker-compose.metadata-local.yml'])
        self.assertEqual(helper.start_command()[-9:], ['up', '-d', '--no-build', '--pull', 'missing', '--wait', '--wait-timeout', '120', 'kai-metadata'])
        command = helper.password_command('kai_binding_runtime')
        self.assertIn('\\password kai_binding_runtime', command[-1])
        self.assertNotIn('a' * 64, ' '.join(command))
        self.assertIn('POSTGRES_PASSWORD', command[-1])

    def test_refuses_volume_owned_by_another_project(self):
        self.setup.validate_volume_metadata({'Name': 'kai_metadata_pgdata', 'Driver': 'local', 'Scope': 'local', 'Options': None, 'Labels': {
            'com.docker.compose.project': 'kai', 'com.docker.compose.volume': 'kai-metadata-pgdata'}})
        for labels in ({}, {'com.docker.compose.project': 'foreign', 'com.docker.compose.volume': 'kai-metadata-pgdata'}):
            with self.assertRaises(ValueError):
                self.setup.validate_volume_metadata({'Name': 'kai_metadata_pgdata', 'Labels': labels})

    def test_refuses_network_and_plugin_volume_storage(self):
        base = {'Name': 'kai_metadata_pgdata', 'Driver': 'local', 'Scope': 'local', 'Options': None,
                'Labels': {'com.docker.compose.project': 'kai', 'com.docker.compose.volume': 'kai-metadata-pgdata'}}
        for changes in ({'Driver': 'nfs-plugin'}, {'Scope': 'global'}, {'Options': {'type': 'nfs', 'device': 'host:/external'}}, {'Options': {'type': 'none', 'o': 'bind', 'device': '/external'}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.setup.validate_volume_metadata({**base, **changes})

    def test_provision_sends_secrets_only_via_stdin_and_migrations_as_owner(self):
        helper = self.setup.LocalMetadata(ROOT, {})
        fields = {**self.fields(), **self.setup.configuration_updates(self.fields())}
        calls = []
        helper.run = lambda command, input=None: calls.append((command, input)) or ''
        helper.provision(fields)
        for name in self.setup.RUNTIME_ROLES:
            password = self.setup.bootstrap.value(fields[name]).rsplit('=', 1)[1]
            self.assertFalse(any(password in ' '.join(command) for command, _ in calls))
            self.assertIn(password + '\n' + password + '\n', [data for _, data in calls])
        scripts = [data for _, data in calls if data]
        self.assertIn('SET ROLE kai_migrator;\n\\i /migrations/migrate_identity.sql\n', scripts)
        self.assertIn('SET ROLE kai_migrator;\n\\i /migrations/migrate_admin_warehouse.sql\n', scripts)
        self.assertIn('SET ROLE kai_migrator;\n\\i /migrations/migrate.sql\n', scripts)
        self.assertIn('SET ROLE kai_migrator;\n\\i /migrations/grant_catalog_access.sql\n', scripts)
        self.assertFalse(any('kai_approval_runtime' in ' '.join(command) for command, _ in calls))
        self.assertFalse(any('restart' in command for command, _ in calls))
        self.assertIn('--network', calls[2][0])
        self.assertIn('none', calls[2][0])

    def test_overlay_is_isolated_persistent_and_only_metadata_receives_admin_secret(self):
        try:
            subprocess.run(['docker', 'compose', 'version'], capture_output=True, text=True)
        except FileNotFoundError:
            self.skipTest('Docker CLI unavailable')
        # Standalone overlay intentionally references the MCP service; merge through full base for render.
        import json
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / 'fixture.env'
            env.write_text('PORT=3080\nUID=1000\nGID=1000\nPOSTGRES_DB=fixture\nPOSTGRES_USER=fixture\nPOSTGRES_PASSWORD=fixture\nKAI_METADATA_ADMIN_PASSWORD=' + 'a' * 64 + '\n')
            helper = self.setup.LocalMetadata(ROOT)
            command = helper.compose.copy()
            command[command.index('--env-file') + 1] = str(env)
            rendered = subprocess.run(command + ['config', '--format', 'json'], cwd=ROOT, env=helper.environment, capture_output=True, text=True)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            config = json.loads(rendered.stdout)
        metadata = config['services']['kai-metadata']
        self.assertEqual(metadata['network_mode'], 'none')
        self.assertNotIn('ports', metadata)
        self.assertEqual(metadata['environment']['POSTGRES_PASSWORD'], 'a' * 64)
        self.assertNotIn('KAI_METADATA_ADMIN_PASSWORD', config['services']['clickhouse-mcp']['environment'])
        self.assertEqual(config['volumes']['kai-metadata-binding-keys']['name'], 'kai_metadata_binding_keys')
        self.assertTrue(all(v['read_only'] for v in config['services']['clickhouse-mcp']['volumes'] if v['target'].startswith('/run/kai-')))
