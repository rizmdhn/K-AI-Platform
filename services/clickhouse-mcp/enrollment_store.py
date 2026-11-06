"""Narrow enrollment functions only; never grant this login raw table or approval writes."""

import os
import psycopg
from database import run_database
from approval_store import ApprovalError, storage_error

SQL = {
    "status": "SELECT kai.warehouse_status(%s,%s,%s,%s) AS decision",
    "prepare": "SELECT kai.warehouse_prepare(%s,%s,%s,%s,%s,%s) AS decision",
    "save": "SELECT kai.warehouse_save(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) AS decision",
    "revoke": "SELECT kai.warehouse_revoke(%s,%s,%s,%s) AS decision",
}


async def operation(name, args):
    dsn = os.getenv("KAI_ENROLLMENT_DSN")
    if not dsn:
        raise ApprovalError(503, "Warehouse enrollment storage is not configured.")

    async def execute(connection):
        cursor = await connection.execute(SQL[name], args)
        row = await cursor.fetchone()
        decision = row["decision"]
        if not isinstance(decision, dict) or decision.get("state") not in (
            "not_prepared",
            "ready",
            "active",
            "revoked",
        ):
            raise ValueError()
        return decision

    try:
        return await run_database(
            dsn,
            execute,
            readonly=name == "status",
            application_name="kai-warehouse-enrollment",
        )
    except psycopg.Error as error:
        raise storage_error(error, {
            403: "Warehouse authority is required.",
            409: "The warehouse mapping changed.",
            400: "Invalid enrollment request.",
            503: "Enrollment storage is unavailable.",
        }) from None
    except Exception:
        raise ApprovalError(503, "Enrollment storage is unavailable.") from None


def issuer():
    value = os.getenv("KAI_LIBRECHAT_ISSUER", "librechat-local")
    if not value.strip():
        raise ApprovalError(503, "Warehouse issuer is not configured.")
    return value


async def status(actor, subject, username):
    return await operation("status", (actor, subject, username, issuer()))


async def prepare(actor, subject, username, service, request):
    return await operation(
        "prepare", (actor, subject, username, service, issuer(), request)
    )


async def save(
    actor, subject, username, service, binding, revision, cipher, key, request
):
    return await operation(
        "save",
        (
            actor,
            subject,
            username,
            service,
            issuer(),
            binding,
            revision,
            cipher,
            key,
            request,
        ),
    )


async def revoke(actor, subject, request):
    return await operation("revoke", (actor, subject, issuer(), request))
