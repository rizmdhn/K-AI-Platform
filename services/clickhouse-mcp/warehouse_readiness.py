"""Admin-only local configuration checks; never verifies a database or cloud."""

import asyncio

from starlette.responses import JSONResponse

from approval_store import ApprovalError
from binding_preflight import check
from native_warehouse import native_context


# Fixed public names and codes prevent private configuration values escaping.
_CHECKS = (
    ('binding_dsn', 'KAI_BINDING_DSN', 'configured', 'missing_or_invalid'),
    ('enrollment_dsn', 'KAI_ENROLLMENT_DSN', 'configured', 'missing_or_invalid'),
    ('distinct_runtime_users', 'distinct_runtime_users', 'distinct', 'missing_or_shared'),
    ('keyring', 'keyring', 'active_key_available', 'active_key_unavailable'),
    ('warehouse_target', 'warehouse_target', 'https_configured', 'missing_or_invalid'),
)


async def readiness_request(request):
    try:
        await native_context(
            request.headers.get('authorization'), request.path_params.get('subject'), False
        )
        checks = await asyncio.to_thread(check)
        result = {
            'version': 2,
            'scope': 'local_configuration_only',
            'database_verified': False,
            'cloud_verified': False,
            'checks': {
                name: {'ok': checks.get(source) is True,
                       'code': success if checks.get(source) is True else failure}
                for name, source, success, failure in _CHECKS
            },
        }
        status = 200
    except ApprovalError as error:
        status, result = error.status, {'message': str(error)}
    except Exception:
        status, result = 503, {'message': 'Warehouse configuration checks are unavailable.'}
    return JSONResponse(result, status_code=status, headers={'Cache-Control': 'no-store'})


def register_readiness_routes(mcp):
    mcp.custom_route('/v1/admin/warehouse-users/{subject}/readiness', methods=['GET'])(
        readiness_request
    )
