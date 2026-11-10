import os,unittest
from unittest.mock import patch,AsyncMock
from uuid import uuid4
from starlette.requests import Request
import warehouse_enrollment as api
class AgentEligibilityTests(unittest.IsolatedAsyncioTestCase):
 async def test_fresh_read_only_receipt_changes_with_binding_revision(self):
  self.assertTrue(callable(getattr(api,'warehouse_eligibility',None)))
  context={'actor_id':'a'*24,'target_id':'b'*24,'status':'active','warehouse':{'service':'staging.example','username':'personal'}}
  decision={'state':'active','credentials_saved':True,'ch_username':'personal','service_target':'https://staging.example:8443','binding_id':str(uuid4()),'revision':str(uuid4()),'password':'never'}
  request=Request({'type':'http','method':'GET','path':'/eligibility','headers':[(b'authorization',b'Bearer fixture')],'path_params':{'subject':'b'*24}})
  with patch.dict(os.environ,{'CLICKHOUSE_HOST':'staging.example','CLICKHOUSE_PORT':'8443','CLICKHOUSE_SECURE':'true','CLICKHOUSE_VERIFY':'true'}),patch.object(api,'native_context',AsyncMock(return_value=context)),patch.object(api.store,'status',AsyncMock(return_value=decision)),patch.object(api,'verify_clickhouse',AsyncMock(side_effect=AssertionError('must not contact CH'))) as verify:
   response=await api.warehouse_eligibility(request)
   import json
   value=json.loads(response.body);self.assertTrue(value['eligible']);self.assertEqual(value['revision'],decision['revision']);self.assertNotIn('never',str(value));verify.assert_not_awaited()
   decision['state']='revoked';response=await api.warehouse_eligibility(request);value=json.loads(response.body);self.assertFalse(value['eligible']);self.assertIsNone(value['revision'])
