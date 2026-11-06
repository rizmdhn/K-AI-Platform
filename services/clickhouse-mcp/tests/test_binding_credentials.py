import asyncio
import gc
import json
import os
import unittest
from unittest import mock
from uuid import uuid4

import binding_credentials as bindings
import per_user_middleware as mw
from credential_crypto import BindingContext, encrypt_password
from test_audit import run, returning
from tool_errors import GatewayError

UID = 'a' * 24


class BindingCredentialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = mock.patch.dict(os.environ, {
            'KAI_BINDING_DSN': 'postgresql://fixture.invalid/unused',
            'KAI_BINDING_KEY_DIR': '/fixture-keys', 'KAI_LIBRECHAT_ISSUER': 'librechat-local',
            'CLICKHOUSE_HOST':'staging.example','CLICKHOUSE_PORT':'8443',
            'CLICKHOUSE_SECURE':'true','CLICKHOUSE_VERIFY':'true',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.native=mock.patch.object(bindings,'verify_native_binding',mock.AsyncMock(),create=True)
        self.native.start();self.addCleanup(self.native.stop)
        context = BindingContext(uuid4(), 'approved_alice', 1, uuid4(),'https://staging.example:8443')
        self.keys = {1: os.urandom(32)}
        self.row = dict(principal_id=context.principal_id, ch_username=context.ch_username,
                        key_version=1, binding_id=context.binding_id,service_target=context.service_target,
                        cipher_blob=encrypt_password('private-password', context, self.keys))

    async def test_fresh_lookup_and_decrypt_on_each_call_without_cache(self):
        fetch = mock.AsyncMock(side_effect=[[self.row], []])
        with mock.patch.object(bindings, '_fetch_binding', fetch), \
             mock.patch.object(bindings, 'load_keyring', return_value=self.keys):
            self.assertEqual(await bindings.get_managed_credentials(UID), ('approved_alice', 'private-password'))
            with self.assertRaises(GatewayError) as caught:
                await bindings.get_managed_credentials(UID)
        self.assertEqual(caught.exception.code, 'AUTH_REQUIRED')
        self.assertEqual(fetch.await_count, 2)

    async def test_missing_or_malformed_identity_never_queries_store(self):
        fetch = mock.AsyncMock()
        with mock.patch.object(bindings, '_fetch_binding', fetch):
            for uid in (None, '', 'u1', 'A' * 24, UID + "' OR 1=1", '{{LIBRECHAT_USER_ID}}'):
                with self.assertRaises(GatewayError):
                    await bindings.get_managed_credentials(uid)
        fetch.assert_not_awaited()

    async def test_unconfigured_store_or_keys_fails_closed_without_fallback(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(bindings, '_fetch_binding', mock.AsyncMock()) as fetch:
            with self.assertRaises(GatewayError) as caught:
                await bindings.get_managed_credentials(UID)
            self.assertEqual(caught.exception.code, 'DEPENDENCY_UNAVAILABLE')
        fetch.assert_not_awaited()

    async def test_zero_or_ambiguous_rows_are_denied_before_keys_are_loaded(self):
        for rows in ([], [self.row, self.row]):
            with mock.patch.object(bindings, '_fetch_binding', mock.AsyncMock(return_value=rows)), \
                 mock.patch.object(bindings, 'load_keyring') as keys:
                with self.assertRaises(GatewayError) as caught:
                    await bindings.get_managed_credentials(UID)
                self.assertEqual(caught.exception.code, 'AUTH_REQUIRED')
                keys.assert_not_called()

    async def test_dependency_and_secret_tamper_errors_never_echo_secrets(self):
        secret_error = RuntimeError('postgresql://user:secret-password@host cipher-contents')
        with mock.patch.object(bindings, '_fetch_binding', mock.AsyncMock(side_effect=secret_error)):
            with self.assertRaises(GatewayError) as caught:
                await bindings.get_managed_credentials(UID)
            self.assertNotIn('secret-password', str(caught.exception))
            self.assertEqual(caught.exception.code, 'DEPENDENCY_UNAVAILABLE')
        bad_row = dict(self.row, ch_username='other-person')
        with mock.patch.object(bindings, '_fetch_binding', mock.AsyncMock(return_value=[bad_row])), \
             mock.patch.object(bindings, 'load_keyring', return_value=self.keys):
            with self.assertRaises(GatewayError) as caught:
                await bindings.get_managed_credentials(UID)
            self.assertNotIn('private-password', str(caught.exception))

    async def test_timeout_is_bounded_and_cancellation_propagates(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            entered.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await release.wait()  # Models a driver waiting to drain cancellation.
                raise
        connection = mock.AsyncMock()
        connection.__aenter__.return_value = connection
        connection.execute.side_effect = slow
        try:
            with mock.patch.object(bindings.database.psycopg.AsyncConnection, 'connect', mock.AsyncMock(return_value=connection)), \
                 mock.patch.object(bindings, 'LOOKUP_TIMEOUT_SECONDS', 0.01):
                task = asyncio.create_task(bindings.get_managed_credentials(UID))
                await entered.wait()
                done, _ = await asyncio.wait({task}, timeout=0.2)
                self.assertIn(task, done, 'lookup deadline must not wait for slow driver cancellation')
                with self.assertRaises(GatewayError):
                    task.result()
                connection.close.assert_awaited()
        finally:
            release.set()
            if not task.done():
                try:
                    await task
                except GatewayError:
                    pass
        with mock.patch.object(bindings, '_fetch_binding', mock.AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await bindings.get_managed_credentials(UID)

    async def test_actual_driver_contract_is_parameterized_readonly_and_closes_connection(self):
        connection = mock.AsyncMock()
        connection.__aenter__.return_value = connection
        cursor = mock.AsyncMock()
        cursor.fetchall.return_value = [self.row]
        connection.execute.return_value = cursor
        connect = mock.AsyncMock(return_value=connection)
        with mock.patch.object(bindings.database.psycopg.AsyncConnection, 'connect', connect):
            rows = await bindings._fetch_binding('fixture-dsn', UID, 'librechat-local')
        self.assertEqual(rows, [self.row])
        sql, parameters = connection.execute.await_args.args
        self.assertNotIn(UID, sql)
        self.assertIn('kai.active_ch_bindings', sql)
        self.assertIn('service_target', sql)
        self.assertEqual(parameters, ('librechat-local', UID, UID))
        self.assertTrue(connect.await_args.kwargs['autocommit'])
        self.assertEqual(connect.await_args.kwargs['sslmode'], 'verify-full')
        self.assertIn('default_transaction_read_only=on', connect.await_args.kwargs['options'])
        connection.close.assert_awaited_once()

    async def test_wrong_or_legacy_service_is_denied_before_native_or_key_lookup(self):
        for target in (None,'https://production.example:8443'):
            row=dict(self.row,service_target=target)
            with mock.patch.object(bindings,'_fetch_binding',mock.AsyncMock(return_value=[row])), \
                 mock.patch.object(bindings,'load_keyring') as keys, \
                 mock.patch.object(bindings,'verify_native_binding',mock.AsyncMock(),create=True) as native:
                with self.assertRaises(GatewayError):await bindings.get_managed_credentials(UID)
                keys.assert_not_called();native.assert_not_awaited()

    async def test_caller_cancellation_closes_socket_before_slow_driver_cleanup(self):
        entered, release = asyncio.Event(), asyncio.Event()
        events = []
        connection = mock.AsyncMock()
        async def close():
            events.append('closed')
        async def execute(*args):
            entered.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                events.append('cancelled')
                await release.wait()
                raise
        connection.close.side_effect = close
        connection.execute.side_effect = execute
        with mock.patch.object(bindings.database.psycopg.AsyncConnection, 'connect', mock.AsyncMock(return_value=connection)):
            task = asyncio.create_task(bindings.get_managed_credentials(UID))
            try:
                await entered.wait()
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=0.2)
                self.assertIn(task, done)
                with self.assertRaises(asyncio.CancelledError):
                    task.result()
                self.assertTrue(events and events[0] == 'closed')
            finally:
                release.set()

    async def test_cancellation_racing_completed_failure_does_not_log_raw_exception(self):
        loop = asyncio.get_running_loop()
        prior = loop.get_exception_handler()
        diagnostics = []
        loop.set_exception_handler(lambda _, context: diagnostics.append(context))
        async def cancel_wait(tasks, timeout):
            await asyncio.sleep(0)  # Driver fails before cancellation reaches the waiting caller.
            raise asyncio.CancelledError
        try:
            with mock.patch.object(bindings.database.psycopg.AsyncConnection, 'connect', mock.AsyncMock(side_effect=RuntimeError('private-dsn-password'))), \
                 mock.patch.object(bindings.asyncio, 'wait', cancel_wait):
                with self.assertRaises(asyncio.CancelledError):
                    await bindings._fetch_binding('fixture', UID, 'librechat-local')
            await asyncio.sleep(0)
            gc.collect()
            self.assertEqual(diagnostics, [], 'completed driver errors must be consumed during cancellation')
        finally:
            loop.set_exception_handler(prior)


class ManagedBindingMiddlewareTests(unittest.TestCase):
    def test_unset_mode_defaults_to_managed_not_personal_headers(self):
        resolve = mock.AsyncMock(return_value=('approved_alice', 'private-password'))
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(mw, 'get_managed_credentials', resolve):
            record, state, error = run('run_query', {'query': 'SELECT 1'},
                {'x-librechat-user-id': UID, 'x-ch-user': 'forged', 'x-ch-password': 'forged'}, returning([[1]]))
        self.assertIsNone(error)
        resolve.assert_awaited_once_with(UID)
        self.assertEqual(state[mw.CLIENT_CONFIG_OVERRIDES_KEY]['username'], 'approved_alice')

    def test_managed_backend_auth_rejection_directs_user_to_administrator(self):
        next_call = mock.AsyncMock(side_effect=mw.ToolError('AUTHENTICATION_FAILED sensitive-password'))
        with mock.patch.dict(os.environ, {'KAI_BINDING_MODE': 'managed'}), \
             mock.patch.object(mw, 'get_managed_credentials', mock.AsyncMock(return_value=('alice','secret'))):
            record, _, error = run('run_query', {'query': 'SELECT 1'}, {'x-librechat-user-id': UID}, next_call)
        self.assertEqual(record['error_code'], 'AUTH_REQUIRED')
        self.assertIn('administrator', str(error))
        self.assertNotIn('Check your credentials', str(error))
        self.assertNotIn('sensitive-password', str(error))

    def test_managed_setup_problems_keep_their_specific_message(self):
        message = 'Warehouse access is not set up for your account yet. Contact your administrator.'
        with mock.patch.dict(os.environ, {'KAI_BINDING_MODE': 'managed'}), \
             mock.patch.object(mw, 'get_managed_credentials', mock.AsyncMock(side_effect=GatewayError('AUTH_REQUIRED', message))):
            _, _, error = run('run_query', {'query': 'SELECT 1'}, {'x-librechat-user-id': UID}, mock.AsyncMock())
        self.assertEqual(json.loads(str(error))['message'], message)

    def test_manual_backend_auth_rejection_points_to_tool_settings(self):
        next_call = mock.AsyncMock(side_effect=mw.ToolError('AUTHENTICATION_FAILED sensitive-password'))
        with mock.patch.dict(os.environ, {'KAI_BINDING_MODE': 'manual'}):
            _, _, error = run('run_query', {'query': 'SELECT 1'}, {'x-ch-user': 'alice', 'x-ch-password': 'pw'}, next_call)
        self.assertIn("this tool's settings", json.loads(str(error))['message'])
        self.assertNotIn('sensitive-password', str(error))

    def test_managed_mode_ignores_forged_ch_headers_and_uses_resolved_identity(self):
        headers = {'x-librechat-user-id': UID, 'x-ch-user': 'forged-user', 'x-ch-password': 'forged-password'}
        resolve = mock.AsyncMock(return_value=('approved_alice', 'private-password'))
        with mock.patch.dict(os.environ, {'KAI_BINDING_MODE': 'managed'}), \
             mock.patch.object(mw, 'get_managed_credentials', resolve):
            record, state, error = run('run_query', {'query': 'SELECT 1'}, headers, returning([[1]]))
        self.assertIsNone(error)
        override = state[mw.CLIENT_CONFIG_OVERRIDES_KEY]
        self.assertEqual(override['username'], 'approved_alice')
        self.assertEqual(override['password'], 'private-password')
        self.assertEqual(override['settings']['readonly'], 1)
        self.assertEqual(record['ch_user'], 'approved_alice')
        self.assertNotIn('private-password', str(record))
        self.assertNotIn('forged-user', str(record))
        resolve.assert_awaited_once_with(UID)

    def test_denial_never_falls_back_to_headers_or_dispatches_tool(self):
        headers = {'x-librechat-user-id': UID, 'x-ch-user': 'valid-legacy-user', 'x-ch-password': 'password'}
        next_call = mock.AsyncMock()
        with mock.patch.dict(os.environ, {'KAI_BINDING_MODE': 'managed'}), \
             mock.patch.object(mw, 'get_managed_credentials', mock.AsyncMock(side_effect=GatewayError('AUTH_REQUIRED', 'Contact your administrator.'))):
            record, state, error = run('run_query', {'query': 'SELECT 1'}, headers, next_call)
        self.assertIsNotNone(error)
        self.assertEqual(record['error_code'], 'AUTH_REQUIRED')
        self.assertEqual(state, {})
        next_call.assert_not_awaited()

    def test_unsafe_sql_rejects_before_binding_lookup_or_decryption(self):
        resolve = mock.AsyncMock()
        with mock.patch.dict(os.environ, {'KAI_BINDING_MODE': 'managed'}), \
             mock.patch.object(mw, 'get_managed_credentials', resolve):
            record, state, error = run('run_query', {'query': 'DROP TABLE t'}, {'x-librechat-user-id': UID}, mock.AsyncMock())
        self.assertEqual(record['error_code'], 'UNSAFE_SQL')
        resolve.assert_not_awaited()
        self.assertEqual(state, {})

    def test_invalid_binding_mode_refuses_startup(self):
        with mock.patch.dict(os.environ, {'KAI_BINDING_MODE': 'typo'}):
            with self.assertRaises(RuntimeError):
                mw.setup_middleware(mock.Mock())
