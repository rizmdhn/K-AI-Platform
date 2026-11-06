import unittest
from unittest.mock import AsyncMock, patch

class RevocationContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_context_uses_write_authorized_endpoint_and_retains_target(self):
        from warehouse_revocation import revocation_context
        data = dict(actor_id='a'*24, target_id='b'*24, status='disabled', warehouse=None)
        with patch('warehouse_revocation.native_request', AsyncMock(return_value=data)) as request:
            self.assertEqual(await revocation_context('Bearer fixture', 'b'*24), data)
            request.assert_awaited_once_with('Bearer fixture', '/api/admin/users/'+'b'*24+'/kai-warehouse-revoke-context', 'POST')

    async def test_untrusted_subject_or_response_is_denied_before_storage(self):
        from warehouse_revocation import revocation_context
        from approval_store import ApprovalError
        data = dict(actor_id='a'*24, target_id='b'*24, status='disabled', warehouse=None)
        for changes in ({'target_id':'c'*24}, {'actor_id':None}, {'status':'missing'}, {'warehouse':{'service':'', 'username':'x'}}, {'password':'never'}):
            with patch('warehouse_revocation.native_request', AsyncMock(return_value=dict(data, **changes))):
                with self.assertRaises(ApprovalError): await revocation_context('Bearer fixture','b'*24)
        with patch('warehouse_revocation.native_request', AsyncMock()) as request:
            with self.assertRaises(ApprovalError): await revocation_context('Bearer fixture','../user')
            request.assert_not_awaited()
