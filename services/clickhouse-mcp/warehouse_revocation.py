"""Write-authorized native context for cleanup, including disabled targets only."""
import re
from approval_store import ApprovalError
from native_warehouse import native_request


async def revocation_context(authorization, subject):
    if not isinstance(subject, str) or not re.fullmatch(r'[0-9a-f]{24}', subject):
        raise ApprovalError(400, 'Invalid platform user.')
    data = await native_request(authorization,
        f'/api/admin/users/{subject}/kai-warehouse-revoke-context', 'POST')
    if (not isinstance(data, dict)
        or set(data) != {'actor_id', 'target_id', 'status', 'warehouse'}
        or not isinstance(data['actor_id'], str)
        or not re.fullmatch(r'[0-9a-f]{24}', data['actor_id'])
        or data['target_id'] != subject
        or data['status'] not in ('pending', 'active', 'existing', 'disabled')):
        raise ApprovalError(503, 'Platform account verification is unavailable.')
    ref = data['warehouse']
    if ref is not None and (not isinstance(ref, dict) or set(ref) != {'service', 'username'}
        or not all(isinstance(ref[k], str) and ref[k] and '\x00' not in ref[k] for k in ref)
        or len(ref['service']) > 253 or len(ref['username'].encode()) > 1024):
        raise ApprovalError(503, 'Platform account verification is unavailable.')
    return data
