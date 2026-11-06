import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class BindingPreflightTests(unittest.TestCase):
    def test_private_keys_distinct_users_and_explicit_target_without_connections(self):
        spec = importlib.util.find_spec('binding_preflight')
        self.assertIsNotNone(spec, 'binding preflight must exist')
        from binding_preflight import check
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory)/'1.key'
            key.write_bytes(b'k'*32)
            key.chmod(0o600)
            env = {'KAI_BINDING_KEY_DIR':directory,'KAI_BINDING_ACTIVE_KEY_VERSION':'1',
                   'CLICKHOUSE_HOST':'fixture.invalid','CLICKHOUSE_PORT':'8443',
                   'KAI_BINDING_DSN':'host=metadata.invalid user=lookup password=private',
                   'KAI_ENROLLMENT_DSN':'host=metadata.invalid user=enroll password=private'}
            with patch.dict(os.environ,env,clear=True), patch('psycopg.AsyncConnection.connect',side_effect=AssertionError('must not connect')):
                self.assertTrue(all(check().values()))
                with patch.dict(os.environ,{'KAI_ENROLLMENT_DSN':env['KAI_BINDING_DSN']}):
                    self.assertFalse(check()['distinct_runtime_users'])
                with patch.dict(os.environ,{'KAI_BINDING_ACTIVE_KEY_VERSION':'2'}):
                    self.assertFalse(check()['keyring'])
                key.chmod(0o644)
                self.assertFalse(check()['keyring'])

    def test_missing_or_malformed_settings_are_boolean_only(self):
        spec = importlib.util.find_spec('binding_preflight')
        self.assertIsNotNone(spec, 'binding preflight must exist')
        from binding_preflight import check
        with patch.dict(os.environ,{'KAI_ENROLLMENT_DSN':'secret invalid dsn'},clear=True):
            result=check()
            self.assertTrue(all(type(value) is bool for value in result.values()))
            self.assertFalse(any(result.values()))
