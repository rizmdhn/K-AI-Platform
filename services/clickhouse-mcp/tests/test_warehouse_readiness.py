"""Readiness is authenticated local configuration, never database/cloud proof."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import httpx2 as httpx
from starlette.applications import Starlette
from starlette.routing import Route


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('warehouse_readiness'),
                             'Local configuration readiness endpoint is missing')
        import warehouse_readiness
        self.api = warehouse_readiness
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        key = Path(self.directory.name) / '1.key'
        key.write_bytes(b'k' * 32)
        key.chmod(0o600)
        self.env = {
            'KAI_LIBRECHAT_INTERNAL_URL': 'http://api:3080',
            'KAI_BINDING_KEY_DIR': self.directory.name,
            'KAI_BINDING_ACTIVE_KEY_VERSION': '1',
            'KAI_BINDING_DSN': 'host=private.invalid user=lookup_private password=private_password',
            'KAI_ENROLLMENT_DSN': 'host=private.invalid user=enroll_private password=private_password',
            'CLICKHOUSE_HOST': 'private-warehouse.invalid',
            'CLICKHOUSE_PORT': '8443',
            'CLICKHOUSE_SECURE': 'true',
            'CLICKHOUSE_VERIFY': 'true',
            'CLICKHOUSE_MCP_AUTH_TOKEN': 'service-private-token',
        }
        self.native_calls = []
        self.native_status = 200
        self.native_body = {'actor_id': 'a' * 24, 'target_id': 'b' * 24,
                            'status': 'active', 'warehouse': None}

    async def request(self, authorization='Bearer admin', method='GET', env=None):
        routes = []
        class Registry:
            def custom_route(self, path, methods):
                def register(endpoint):
                    routes.append(Route(path, endpoint, methods=methods))
                    return endpoint
                return register
        self.api.register_readiness_routes(Registry())
        app = Starlette(routes=routes)
        real_client = httpx.AsyncClient
        def native(request):
            self.native_calls.append(request)
            if request.headers.get('authorization') == 'Bearer user':
                return httpx.Response(403, json={'message': 'private-denial'})
            return httpx.Response(self.native_status, json=self.native_body)
        def native_client(**kwargs):
            return real_client(transport=httpx.MockTransport(native), **kwargs)
        async with real_client(transport=httpx.ASGITransport(app=app),
                               base_url='http://fixture') as client:
            with mock.patch.dict(os.environ, self.env if env is None else env, clear=True), \
                 mock.patch('native_warehouse.httpx.AsyncClient', side_effect=native_client), \
                 mock.patch('psycopg.AsyncConnection.connect', side_effect=AssertionError('database contacted')), \
                 mock.patch('psycopg.Connection.connect', side_effect=AssertionError('database contacted')), \
                 mock.patch('clickhouse_connect.get_client', side_effect=AssertionError('warehouse contacted')):
                return await client.request(method, '/v1/admin/warehouse-users/' + 'b' * 24 + '/readiness',
                                            headers={'Authorization': authorization} if authorization else {})

    async def test_admin_configuration_success_is_not_remote_verification(self):
        response = await self.request()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['version'], 2)
        self.assertEqual(data['scope'], 'local_configuration_only')
        self.assertIs(data['database_verified'], False)
        self.assertIs(data['cloud_verified'], False)
        self.assertEqual(set(data['checks']), {'binding_dsn', 'enrollment_dsn',
                                             'distinct_runtime_users', 'keyring', 'warehouse_target'})
        self.assertTrue(all(item['ok'] is True for item in data['checks'].values()))
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(len(self.native_calls), 1)
        native = self.native_calls[0]
        self.assertEqual(native.method, 'GET')
        self.assertEqual(native.url.path, '/api/admin/users/' + 'b' * 24 + '/kai-warehouse-context')
        self.assertEqual(native.headers['authorization'], 'Bearer admin')
        for secret in ('private', self.directory.name, 'lookup_private', 'password', 'service-private-token'):
            self.assertNotIn(secret, response.text)

    async def test_missing_and_non_admin_identity_are_denied(self):
        with mock.patch.object(self.api, 'check', side_effect=AssertionError('must authorize first')):
            for authorization, status in ((None, 401), ('Bearer user', 403)):
                response = await self.request(authorization)
                self.assertEqual(response.status_code, status)
        self.assertEqual(len(self.native_calls), 1)

    async def test_foreign_subject_denied_by_fresh_native_context(self):
        self.native_status = 403
        with mock.patch.object(self.api, 'check', side_effect=AssertionError('must authorize first')):
            self.assertEqual((await self.request()).status_code, 403)

    async def test_mismatched_native_target_fails_closed(self):
        self.native_body['target_id'] = 'c' * 24
        self.assertEqual((await self.request()).status_code, 503)

    async def test_context_network_failure_is_safe_unavailable(self):
        self.native_status = 503
        response = await self.request()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', response.text)

    async def test_missing_configuration_is_reported_without_secrets(self):
        response = await self.request(env={'KAI_LIBRECHAT_INTERNAL_URL': 'http://api:3080'})
        self.assertEqual(response.status_code, 200)
        checks = response.json()['checks']
        self.assertTrue(all(item['ok'] is False for item in checks.values()))
        self.assertEqual(checks['binding_dsn']['code'], 'missing_or_invalid')
        self.assertEqual(checks['distinct_runtime_users']['code'], 'missing_or_shared')
        self.assertEqual(checks['keyring']['code'], 'active_key_unavailable')

    async def test_shared_runtime_user_and_missing_active_key_are_reported(self):
        self.env['KAI_ENROLLMENT_DSN'] = self.env['KAI_BINDING_DSN']
        self.env['KAI_BINDING_ACTIVE_KEY_VERSION'] = '2'
        self.env['CLICKHOUSE_VERIFY'] = 'false'
        checks = (await self.request()).json()['checks']
        self.assertIs(checks['binding_dsn']['ok'], True)
        for name in ('distinct_runtime_users', 'keyring', 'warehouse_target'):
            self.assertIs(checks[name]['ok'], False)

    async def test_preflight_exception_never_echoes_private_values(self):
        with mock.patch.object(self.api, 'check', side_effect=ValueError('private_password@private.invalid')):
            response = await self.request()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', response.text)

    async def test_malformed_configuration_is_boolean_only(self):
        self.env['KAI_BINDING_DSN'] = 'private_password malformed'
        self.env['KAI_BINDING_KEY_DIR'] = '/nonexistent-private-fixture'
        self.env['CLICKHOUSE_HOST'] = 'https://private.invalid'
        response = await self.request()
        self.assertEqual(response.status_code, 200)
        checks = response.json()['checks']
        for name in ('binding_dsn', 'keyring', 'warehouse_target'):
            self.assertIs(checks[name]['ok'], False)
        self.assertNotIn('private', response.text)

    async def test_only_get_registered(self):
        response = await self.request(method='POST')
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.native_calls, [])


if __name__ == '__main__':
    unittest.main()
