"""Keep process liveness separate from the upstream bounded dependency probe."""

from starlette.responses import PlainTextResponse
from mcp_clickhouse.mcp_server import health_check


async def live(request):
    return PlainTextResponse("OK")


async def ready(request):
    return await health_check(request)


def register_health_routes(mcp):
    mcp.custom_route("/health/live", methods=["GET"])(live)
    mcp.custom_route("/health/ready", methods=["GET"])(ready)
