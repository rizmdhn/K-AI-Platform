"""Resolve credentials only from the trusted caller's currently eligible binding."""

import asyncio
import os
import re

import database

from credential_crypto import BindingContext, decrypt_password, load_keyring
from tool_errors import GatewayError
from warehouse_target import configured_service
from native_warehouse import verify_native_binding

LOOKUP_TIMEOUT_SECONDS = 5


async def _fetch_binding(dsn, user_id, issuer):
    async def read(connection):
        cursor = await connection.execute(
            "SELECT binding_id,principal_id,ch_username,cipher_blob,key_version,service_target "
            "FROM kai.active_ch_bindings WHERE issuer=%s AND subject=%s AND librechat_user_id=%s LIMIT 2",
            (issuer, user_id, user_id),
        )
        return await cursor.fetchall()

    return await database.run_database(dsn, read, timeout=LOOKUP_TIMEOUT_SECONDS)


async def get_managed_credentials(user_id):
    if not isinstance(user_id, str) or not re.fullmatch(r"[0-9a-f]{24}", user_id):
        raise GatewayError(
            "AUTH_REQUIRED",
            "Your platform session could not be identified for warehouse access. Sign in again; if it continues, contact your administrator.",
        )
    dsn = os.getenv("KAI_BINDING_DSN")
    key_directory = os.getenv("KAI_BINDING_KEY_DIR")
    issuer = os.getenv("KAI_LIBRECHAT_ISSUER", "librechat-local")
    if not dsn or not key_directory or not issuer:
        raise GatewayError(
            "DEPENDENCY_UNAVAILABLE",
            "Warehouse access is not configured. Contact your administrator.",
        )
    try:
        rows = await _fetch_binding(dsn, user_id, issuer)
        if len(rows) != 1:
            raise GatewayError(
                "AUTH_REQUIRED",
                "Warehouse access is not set up for your account yet. Contact your administrator.",
            )
        row = rows[0]
        service = configured_service()
        if row.get("service_target") != service:
            raise GatewayError(
                "AUTH_REQUIRED",
                "The data warehouse changed, so your access must be set up again. Contact your administrator.",
            )
        await verify_native_binding(user_id, row["ch_username"], service)
        context = BindingContext(
            row["principal_id"],
            row["ch_username"],
            row["key_version"],
            row["binding_id"],
            service,
        )
        keys = await asyncio.to_thread(load_keyring, key_directory)
        password = decrypt_password(row["cipher_blob"], context, keys)
        return row["ch_username"], password
    except GatewayError:
        raise
    except Exception:
        # DB/crypto/OS diagnostics can contain DSNs or secret data; never forward their cause.
        raise GatewayError(
            "DEPENDENCY_UNAVAILABLE",
            "Warehouse connection is unavailable. Contact your administrator.",
        ) from None
