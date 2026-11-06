"""One operator-configured HTTPS warehouse target; never a caller-selected URL."""

import os
import re
from tool_errors import GatewayError


def configured_service():
    host = os.getenv("CLICKHOUSE_HOST", "")
    port = os.getenv("CLICKHOUSE_PORT", "8443")
    if (
        not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?", host)
        or not port.isdigit()
        or not 1 <= int(port) <= 65535
        or any(
            os.getenv(key, "true").lower() != "true"
            for key in ("CLICKHOUSE_SECURE", "CLICKHOUSE_VERIFY")
        )
    ):
        raise GatewayError(
            "DEPENDENCY_UNAVAILABLE", "Warehouse HTTPS target is not configured."
        )
    return f"https://{host.lower()}:{int(port)}"
