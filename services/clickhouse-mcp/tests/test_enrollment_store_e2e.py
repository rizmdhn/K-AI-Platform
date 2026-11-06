"""Real PG+enrollment+crypto with mock native/CH HTTPS. Both containers have no network."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

IMAGE=os.environ.get('KAI_ENROLLMENT_E2E_IMAGE')
if IMAGE:
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'db/tests'))
    from support import LocalPostgresTestCase
else:LocalPostgresTestCase=unittest.TestCase

@unittest.skipUnless(IMAGE,'set KAI_ENROLLMENT_E2E_IMAGE for isolated enrollment integration')
class EnrollmentStoreIntegration(LocalPostgresTestCase):
    SHARE_SOCKET=True
    def test_admin_prepare_verify_lookup_reference_change_and_revoke(self):
        self.sql('\\ir /migrations/migrate_identity.sql\n\\ir /migrations/migrate_approvals.sql\n\\ir /migrations/migrate_enrollment.sql\n\\ir /migrations/migrate_admin_warehouse.sql\n\\ir /migrations/migrate_revocation_fence.sql\n')
        for group in ('binding_roles','enrollment_role'):
            self.execute('\\ir /migrations/create_'+group+'.sql\n',self.database)
        for grant in ('mcp_bindings','enrollment_access'):
            self.sql('\\ir /migrations/grant_'+grant+'.sql\n')
        self.execute('CREATE ROLE enrollment_probe LOGIN IN ROLE kai_enrollment_api; CREATE ROLE lookup_probe LOGIN IN ROLE kai_mcp;')
        self.sql("""INSERT INTO kai.ai_principals(principal_id,issuer,subject,librechat_user_id)
          VALUES('11111111-1111-1111-1111-111111111111','fixture',repeat('a',24),repeat('a',24)),
                ('22222222-2222-2222-2222-222222222222','fixture',repeat('b',24),repeat('b',24));""")
        script=r'''
import asyncio,os,tempfile
from pathlib import Path
from unittest.mock import patch
import httpx2 as httpx
from starlette.applications import Starlette
from starlette.routing import Route
from warehouse_enrollment import warehouse_request
from binding_credentials import get_managed_credentials
from tool_errors import GatewayError

async def main():
    original=httpx.AsyncClient
    target='c'*24
    reference={'service':'staging.example','username':'tester_ro'}
    remote_calls=[]
    platform_status='pending'
    def transport(request):
        if request.url.host=='staging.example':
            remote_calls.append(request)
            assert request.method=='POST' and request.url.params['readonly']=='1'
            return httpx.Response(200,json={'ch_user':'tester_ro','readonly':1})
        assert request.url.host=='api'
        if request.url.path.startswith('/api/internal/'):
            return httpx.Response(200,json={'id':target,'status':platform_status,'warehouse':dict(reference)})
        actor='a'*24
        return httpx.Response(200,json={'actor_id':actor,'target_id':target,'status':platform_status,'warehouse':dict(reference)})
    root='/v1/admin/warehouse-users/{subject}'
    app=Starlette(routes=[Route(root,warehouse_request,methods=['GET','DELETE'])]+
        [Route(root+'/'+action,warehouse_request,methods=['POST']) for action in ('prepare','credentials')])
    with tempfile.TemporaryDirectory() as directory:
        key=Path(directory)/'1.key';key.write_bytes(os.urandom(32));key.chmod(0o600)
        os.environ['KAI_BINDING_KEY_DIR']=directory
        async with original(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as user:
            with patch.object(httpx,'AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(transport),**kw)):
                url='/v1/admin/warehouse-users/'+target
                async def call(action='',method='GET',body=None,actor='owner'):
                    response=await user.request(method,url+action,json=body,headers={'Authorization':'Bearer '+actor})
                    assert 'fixture-secret' not in response.text
                    return response
                result=await call('/prepare','POST',{})
                assert result.status_code==200 and result.json()['state']=='ready',result.text
                assert not remote_calls
                result=await call('/credentials','POST',{'password':'fixture-secret'})
                assert result.status_code==200 and result.json()['state']=='active',result.text
                assert len(remote_calls)==1
                try:await get_managed_credentials(target)
                except GatewayError as error:assert error.code=='AUTH_REQUIRED'
                else:raise AssertionError('Pending platform account retained query access')
                platform_status='active'
                assert await get_managed_credentials(target)==('tester_ro','fixture-secret')
                reference['username']='changed'
                try:await get_managed_credentials(target)
                except GatewayError as error:assert error.code=='AUTH_REQUIRED'
                else:raise AssertionError('Changed reference retained warehouse access')
                platform_status='disabled'
                result=await call('', 'DELETE',{})
                assert result.status_code==200 and result.json()['state']=='revoked',result.text
                try:await get_managed_credentials(target)
                except GatewayError as error:assert error.code=='AUTH_REQUIRED'
                else:raise AssertionError('Revoked binding retained access')
    print('Isolated admin prepare/mockCH verify/AES lookup/reference change/disabled revoke passed.')
asyncio.run(main())
'''
        result=subprocess.run(['docker','run','--rm','--pull','never','--network','none','--read-only','--tmpfs','/tmp',
            '--mount',f'type=bind,src={Path(__file__).resolve().parents[1]},dst=/app,readonly',
            '--user','10001:10001','--mount',f'type=volume,src={self.socket_volume},dst=/pgsocket,readonly',
            '--env',f'KAI_ENROLLMENT_DSN=host=/pgsocket dbname={self.database} user=enrollment_probe',
            '--env',f'KAI_BINDING_DSN=host=/pgsocket dbname={self.database} user=lookup_probe',
            '--env','KAI_LIBRECHAT_ISSUER=fixture','--env','CLICKHOUSE_HOST=staging.example',
            '--env','KAI_PLATFORM_INTERNAL_TOKEN=fixture-token',IMAGE,'python','-c',script],
            capture_output=True,text=True,timeout=50)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('reference change/disabled revoke passed',result.stdout)
        wiped=self.sql("SELECT b.status,b.cipher_blob IS NULL,b.key_version IS NULL,a.revoked_at IS NOT NULL FROM kai.ch_bindings b JOIN kai.ai_principals p USING(principal_id) JOIN kai.approved_ch_accounts a USING(principal_id,ch_username) WHERE p.librechat_user_id=repeat('c',24);")
        self.assertIn('revoked|t|t|t',wiped.stdout)
