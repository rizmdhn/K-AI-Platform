import os
import unittest
from unittest import mock
import httpx2 as httpx
import native_warehouse as native
import warehouse_enrollment as enrollment
from approval_store import ApprovalError
from tool_errors import GatewayError

UID='a'*24

class WarehouseTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env=mock.patch.dict(os.environ,{'CLICKHOUSE_HOST':'staging.example','CLICKHOUSE_PORT':'8443',
            'CLICKHOUSE_SECURE':'true','CLICKHOUSE_VERIFY':'true','KAI_PLATFORM_INTERNAL_TOKEN':'fixture-token'},clear=True)
        self.env.start();self.addCleanup(self.env.stop)
        self.original=httpx.AsyncClient

    async def test_verification_uses_fixed_https_sql_and_never_redirects(self):
        seen=[]
        def respond(request):
            seen.append(request)
            return httpx.Response(200,json={'ch_user':'tester','readonly':'1'})
        with mock.patch.object(enrollment.httpx,'AsyncClient',side_effect=lambda **kw:self.original(transport=httpx.MockTransport(respond),**kw)) as factory:
            await enrollment.verify_clickhouse('tester','private',None)
        self.assertEqual(str(seen[0].url),'https://staging.example:8443/?readonly=1')
        self.assertEqual(seen[0].method,'POST')
        self.assertIn('SELECT currentUser()',seen[0].content.decode())
        self.assertNotIn('private',seen[0].content.decode())
        self.assertFalse(factory.call_args.kwargs['follow_redirects']);self.assertTrue(factory.call_args.kwargs['verify'])
        self.assertFalse(factory.call_args.kwargs['trust_env'])

    async def test_bad_identity_readonly_redirect_or_oversized_reply_is_rejected(self):
        for status,body in ((200,{'ch_user':'other','readonly':1}),(200,{'ch_user':'tester','readonly':0}),
                            (302,{}),(401,{'secret':'private'}),(200,{'secret':'x'*5000})):
            with mock.patch.object(enrollment.httpx,'AsyncClient',side_effect=lambda **kw:self.original(transport=httpx.MockTransport(lambda r:httpx.Response(status,json=body)),**kw)):
                with self.assertRaises(ApprovalError) as caught:await enrollment.verify_clickhouse('tester','private',None)
            self.assertNotIn('private',str(caught.exception))

    async def test_native_context_is_bound_to_target_and_native_capability_route(self):
        seen=[]
        def respond(request):
            seen.append(request)
            return httpx.Response(200,json={'actor_id':'b'*24,'target_id':UID,'status':'active',
                'warehouse':{'service':'staging.example','username':'tester'}})
        with mock.patch.object(native.httpx,'AsyncClient',side_effect=lambda **kw:self.original(transport=httpx.MockTransport(respond),**kw)):
            context=await native.native_context('Bearer fixture',UID,True)
        self.assertEqual(context['actor_id'],'b'*24)
        self.assertEqual(seen[0].method,'POST')
        self.assertEqual(seen[0].url.path,f'/api/admin/users/{UID}/kai-warehouse-context')
        self.assertEqual(seen[0].content,b'{}')

    async def test_native_auth_denial_never_returns_context(self):
        for status in (401,403,404,302):
            with mock.patch.object(native.httpx,'AsyncClient',side_effect=lambda **kw:self.original(transport=httpx.MockTransport(lambda r:httpx.Response(status,json={})),**kw)):
                with self.assertRaises(ApprovalError):await native.native_context('Bearer fixture',UID,True)

    async def test_managed_lookup_rechecks_native_reference_and_activity(self):
        for state,username,service in (('active','tester','staging.example'),('pending','tester','staging.example'),
                                       ('active','other','staging.example'),('active','tester','production.example')):
            profile={'id':UID,'status':state,'warehouse':{'service':service,'username':username}}
            with mock.patch.object(native.httpx,'AsyncClient',side_effect=lambda **kw:self.original(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=profile)),**kw)):
                if state=='active' and username=='tester' and service=='staging.example':
                    await native.verify_native_binding(UID,'tester','https://staging.example:8443')
                else:
                    with self.assertRaises(GatewayError):await native.verify_native_binding(UID,'tester','https://staging.example:8443')

if __name__=='__main__':unittest.main()
