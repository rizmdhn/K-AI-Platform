import importlib.util
import json
import os
import unittest
import asyncio
from types import SimpleNamespace
from unittest import mock
from uuid import UUID, uuid4
from dataclasses import replace
from credential_crypto import BindingContext, CredentialError, decrypt_password

import httpx2 as httpx
from starlette.applications import Starlette
from starlette.routing import Route


class EnrollmentHTTPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('warehouse_enrollment'), 'Warehouse enrollment is not implemented')
        import warehouse_enrollment as api
        self.api = api
        self.env = mock.patch.dict(os.environ, {'CLICKHOUSE_HOST':'staging.example','CLICKHOUSE_PORT':'8443',
            'CLICKHOUSE_SECURE':'true','CLICKHOUSE_VERIFY':'true','KAI_BINDING_ACTIVE_KEY_VERSION':'1',
            'KAI_BINDING_KEY_DIR':'/fixture','KAI_LIBRECHAT_ISSUER':'fixture'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.context = {'actor_id':'a'*24,'target_id':'b'*24,'status':'active',
                        'warehouse':{'service':'staging.example','username':'tester_ro'}}
        self.status = dict(state='ready',principal_id=str(uuid4()),binding_id=str(uuid4()),revision=str(uuid4()),
                           service_target='https://staging.example:8443',ch_username='tester_ro',
                           credentials_saved=False)

    async def request(self, action='', method='GET', body=None, contexts=None, keys=None):
        app=Starlette(routes=[Route('/v1/admin/warehouse-users/{subject}'+action,self.api.warehouse_request,methods=[method])])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
            with mock.patch.object(self.api,'native_context',mock.AsyncMock(return_value=self.context, side_effect=contexts)), \
                 mock.patch.object(self.api,'revocation_context',mock.AsyncMock(return_value=self.context)), \
                 mock.patch.object(self.api.store,'status',mock.AsyncMock(return_value=dict(self.status))) as status, \
                 mock.patch.object(self.api.store,'prepare',mock.AsyncMock(return_value=dict(self.status))) as prepare, \
                 mock.patch.object(self.api.store,'save',mock.AsyncMock(return_value=dict(self.status,state='active',credentials_saved=True))) as save, \
                 mock.patch.object(self.api.store,'revoke',mock.AsyncMock(return_value=dict(self.status,state='revoked',credentials_saved=False))) as revoke, \
                 mock.patch.object(self.api,'verify_clickhouse',mock.AsyncMock()) as verify, \
                 mock.patch.object(self.api,'load_keyring',return_value={1:b'k'*32} if keys is None else keys):
                response=await client.request(method,'/v1/admin/warehouse-users/'+'b'*24+action,
                                              json=body,headers={'Authorization':'Bearer fixture'})
        return response,status,prepare,save,revoke,verify

    async def test_status_and_prepare_never_connect_to_clickhouse(self):
        for action,method,body in (('', 'GET',None),('/prepare','POST',{})):
            response,_,_,_,_,verify=await self.request(action,method,body)
            self.assertEqual(response.status_code,200)
            verify.assert_not_awaited()
            for secret in ('principal_id','binding_id','revision','cipher_blob','password'):
                self.assertNotIn(secret,response.text)
            self.assertEqual(response.headers['cache-control'],'no-store')

    async def test_enroll_verifies_then_saves_encrypted_password_for_exact_context(self):
        response,_,_,save,_,verify=await self.request('/credentials','POST',{'password':'fixture-private'})
        self.assertEqual(response.status_code,200)
        verify.assert_awaited_once_with('tester_ro','fixture-private',mock.ANY)
        self.assertEqual(response.json()['state'],'active')
        self.assertNotIn('fixture-private',response.text)
        args=save.await_args.args
        self.assertNotIn('fixture-private',str(args))
        self.assertTrue(any(isinstance(value,bytes) for value in args))

    async def test_pending_platform_account_can_save_after_admin_prepare(self):
        self.context['status']='pending'
        response,_,_,save,_,verify=await self.request('/credentials','POST',{'password':'fixture-private'})
        self.assertEqual(response.status_code,200)
        verify.assert_awaited_once()
        save.assert_awaited_once()
        self.assertEqual(self.context['status'],'pending')

    async def test_unprepared_and_revoked_bindings_prevent_verification(self):
        for state in ('not_prepared','revoked'):
            self.status['state']=state
            response,_,_,save,_,verify=await self.request('/credentials','POST',{'password':'fixture-private'})
            self.assertEqual(response.status_code,409)
            verify.assert_not_awaited(); save.assert_not_awaited()

    async def test_public_status_has_only_admin_enrollment_fields(self):
        response,*_=await self.request()
        self.assertEqual(response.json(),dict(state='ready',service={'host':'staging.example','port':8443},
            ch_username='tester_ro',credentials_saved=False,reference_matches=True))

    async def test_no_approval_route_is_registered(self):
        routes=[]
        class Registry:
            def custom_route(self,path,methods):
                routes.append((path,methods))
                return lambda endpoint:endpoint
        self.api.register_warehouse_routes(Registry())
        self.assertEqual({path.rsplit('/',1)[-1] for path,_ in routes},
                         {'{subject}','prepare','credentials','eligibility'})

    async def test_native_identity_and_reference_changes_before_and_after_verification_deny_save(self):
        for key,value in (('actor_id','c'*24),('target_id','c'*24),('status','existing'),
                          ('warehouse',{'service':'staging.example','username':'changed'})):
            changed=dict(self.context,**{key:value})
            for contexts, verified in (([self.context,changed],False),
                                       ([self.context,self.context,changed],True)):
                response,_,_,save,_,verify=await self.request('/credentials','POST',
                    {'password':'fixture-private'},contexts=contexts)
                self.assertEqual(response.status_code,409)
                self.assertEqual(verify.await_count,int(verified))
                save.assert_not_awaited()

    async def test_native_denial_before_remote_verification_never_saves_credentials(self):
        response,_,_,save,_,verify=await self.request('/credentials','POST',{'password':'fixture-private'},
            contexts=[self.api.ApprovalError(403,'Platform account access is required.')])
        self.assertEqual(response.status_code,403)
        save.assert_not_awaited();verify.assert_not_awaited()

    async def test_unavailable_or_invalid_active_key_never_verifies_or_saves(self):
        for keys in ({},{1:b'short'},{2:b'k'*32}):
            response,_,_,save,_,verify=await self.request('/credentials','POST',
                {'password':'fixture-private'},keys=keys)
            self.assertEqual(response.status_code,503)
            self.assertNotIn('fixture-private',response.text)
            save.assert_not_awaited();verify.assert_not_awaited()

    async def test_body_rejects_duplicate_keys_and_nonfinite_constants(self):
        for raw in (b'{"password":"first","password":"second"}',b'{"password":NaN}'):
            async def chunks():
                yield raw
            request=SimpleNamespace(headers={'content-type':'application/json'},stream=chunks)
            with self.assertRaises(self.api.ApprovalError) as caught:
                await self.api.body(request,('password',))
            self.assertEqual(caught.exception.status,400)

    async def test_saved_cipher_authenticates_exact_binding_context(self):
        response,_,_,save,_,_=await self.request('/credentials','POST',{'password':'fixture-private'})
        self.assertEqual(response.status_code,200)
        args=save.await_args.args
        binding=BindingContext(UUID(self.status['principal_id']),args[2],args[7],args[4],args[3])
        self.assertEqual(decrypt_password(args[6],binding,{1:b'k'*32}),'fixture-private')
        for altered in (replace(binding,binding_id=uuid4()),replace(binding,principal_id=uuid4()),
                        replace(binding,ch_username='other'),replace(binding,service_target='https://other.example:8443')):
            with self.assertRaises(CredentialError):
                decrypt_password(args[6],altered,{1:b'k'*32})

    async def test_reference_change_and_target_change_deny_enrollment(self):
        for service,username in (('production.example','tester_ro'),('staging.example','other')):
            self.context['warehouse']={'service':service,'username':username}
            response,_,_,save,_,verify=await self.request('/credentials','POST',{'password':'fixture-private'})
            self.assertIn(response.status_code,(400,409))
            verify.assert_not_awaited(); save.assert_not_awaited()

    async def test_forged_fields_or_empty_password_never_reach_verification(self):
        for body in ({'password':''},{'password':'x','username':'forged'},{'password':'x','subject':'c'*24},
                     {'password':'x','service':'evil.example'},{'password':'x'*4097}):
            response,_,_,save,_,verify=await self.request('/credentials','POST',body)
            self.assertIn(response.status_code,(400,413))
            save.assert_not_awaited();verify.assert_not_awaited()

    async def test_revoke_never_connects_and_allows_cleared_reference(self):
        self.context['warehouse']=None
        response,_,_,_,revoke,verify=await self.request('', 'DELETE',{})
        self.assertEqual(response.status_code,200)
        revoke.assert_awaited_once();verify.assert_not_awaited()

    async def test_verification_failure_does_not_save_or_echo_secret(self):
        original=self.api.verify_clickhouse
        async def fail(*args): raise RuntimeError('private-password-server-response')
        self.api.verify_clickhouse=fail
        app=Starlette(routes=[Route('/v1/admin/warehouse-users/{subject}/credentials',self.api.warehouse_request,methods=['POST'])])
        try:
            with mock.patch.object(self.api,'native_context',mock.AsyncMock(return_value=self.context)), \
                 mock.patch.object(self.api.store,'status',mock.AsyncMock(return_value=self.status)), \
                 mock.patch.object(self.api.store,'save',mock.AsyncMock()) as save, \
                 mock.patch.object(self.api,'load_keyring',return_value={1:b'k'*32}):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
                    response=await client.post('/v1/admin/warehouse-users/'+'b'*24+'/credentials',json={'password':'fixture-private'})
            self.assertEqual(response.status_code,503)
            self.assertNotIn('private',response.text);save.assert_not_awaited()
        finally:self.api.verify_clickhouse=original

    async def test_slow_body_has_its_own_receive_deadline(self):
        async def slow():
            yield b'{'
            await asyncio.sleep(10)
        request=SimpleNamespace(headers={'content-type':'application/json'},stream=slow)
        with mock.patch.object(self.api,'BODY_TIMEOUT_SECONDS',.01,create=True):
            with self.assertRaises(self.api.ApprovalError) as caught:
                await asyncio.wait_for(self.api.body(request,()),.1)
        self.assertEqual(caught.exception.status,408)

    async def test_disabled_target_revoke_uses_special_context_without_verification(self):
        self.context['status']='disabled'
        response,_,prepare,save,revoke,verify=await self.request('', 'DELETE', {})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['state'],'revoked')
        revoke.assert_awaited_once(); verify.assert_not_awaited(); save.assert_not_awaited(); prepare.assert_not_awaited()

    async def test_no_binding_revoke_is_a_safe_noop(self):
        self.context['status']='disabled';self.status.update(state='not_prepared',binding_id=None,credentials_saved=False)
        response,_,_,_,revoke,verify=await self.request('', 'DELETE', {})
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['credentials_saved'])
        revoke.assert_awaited_once();verify.assert_not_awaited()


if __name__=='__main__':unittest.main()
