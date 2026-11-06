"""Local configuration/key checks only. Never connects to PostgreSQL, CH or a model."""

import json
import os
import psycopg
from credential_crypto import load_keyring
from warehouse_target import configured_service


def check():
    result = {}
    users = []
    for name in ("KAI_BINDING_DSN", "KAI_ENROLLMENT_DSN"):
        try:
            raw = os.getenv(name, "")
            info = psycopg.conninfo.conninfo_to_dict(raw) if raw else {}
            valid = bool(
                info.get("host") and info.get("user") and not info.get("service")
            )
            result[name] = valid
            users.append(info.get("user") if valid else None)
        except Exception:
            result[name] = False
            users.append(None)
    result["distinct_runtime_users"] = all(users) and len(set(users)) == 2
    try:
        version = int(os.getenv("KAI_BINDING_ACTIVE_KEY_VERSION", "1"))
        keys = load_keyring(os.getenv("KAI_BINDING_KEY_DIR", ""))
        result["keyring"] = version in keys
    except Exception:
        result["keyring"] = False
    try:
        configured_service()
        result["warehouse_target"] = True
    except Exception:
        result["warehouse_target"] = False
    return result


if __name__ == "__main__":
    checks = check()
    print(
        json.dumps(
            {"checks": checks, "scope": "local_configuration_only"}, sort_keys=True
        )
    )
    raise SystemExit(0 if all(checks.values()) else 1)
