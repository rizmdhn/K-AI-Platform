"""Admin-triggered enrollment in the native Users panel. Never registered as an MCP tool."""

import asyncio
import os
import ssl
from urllib.parse import urlsplit
from uuid import UUID, uuid4
import httpx2 as httpx
from starlette.responses import JSONResponse
from access_approvals import _json
from approval_store import ApprovalError
from credential_crypto import BindingContext, encrypt_password, load_keyring
from native_warehouse import native_context
from warehouse_revocation import revocation_context
from warehouse_target import configured_service
import enrollment_store as store

BODY_TIMEOUT_SECONDS = 5


def _tls():
    # Same private CA as the query driver (CLICKHOUSE_CA_CERT), e.g. an on-prem warehouse; else public CAs.
    ca = os.getenv("CLICKHOUSE_CA_CERT")
    return ssl.create_default_context(cafile=ca) if ca else True


async def verify_clickhouse(username, password, request_id):
    service = configured_service()
    try:
        async with asyncio.timeout(6):
            async with httpx.AsyncClient(
                timeout=3, trust_env=False, follow_redirects=False, verify=_tls(),
            ) as client:
                async with client.stream(
                    "POST",
                    service + "/",
                    params={"readonly": "1"},
                    auth=httpx.BasicAuth(username, password),
                    headers={"Content-Type": "text/plain"},
                    content="SELECT currentUser() AS ch_user, getSetting('readonly') AS readonly FORMAT JSONEachRow",
                ) as response:
                    if response.status_code in (401, 403):
                        raise ApprovalError(
                            400, "ClickHouse login or access was rejected."
                        )
                    if response.status_code != 200:
                        raise ValueError()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 4096:
                            raise ValueError()
                        data.extend(chunk)
        result = _json(data)
        if (
            not isinstance(result, dict)
            or set(result) != {"ch_user", "readonly"}
            or result["ch_user"] != username
            or str(result["readonly"]) != "1"
        ):
            raise ApprovalError(
                400, "ClickHouse identity or read-only verification failed."
            )
    except ApprovalError:
        raise
    except Exception:
        raise ApprovalError(503, "ClickHouse verification is unavailable.") from None


def public_status(decision, context, service):
    ref = context["warehouse"]
    actual = decision.get("service_target") or service
    target = urlsplit(actual)
    match = bool(
        ref
        and (
            decision.get("binding_id") is None
            or decision.get("service_target") is not None
        )
        and ref["username"] == decision.get("ch_username")
        and ref["service"] == urlsplit(service).hostname
        and actual == service
    )
    return dict(
        state=(
            "reference_required"
            if ref is None and decision.get("binding_id") is None
            else decision["state"]
        ),
        service={"host": target.hostname, "port": target.port},
        ch_username=decision.get("ch_username"),
        credentials_saved=decision.get("credentials_saved") is True,
        reference_matches=match
    )


async def body(request, keys):
    if (
        request.headers.get("content-type", "").split(";")[0].lower()
        != "application/json"
    ):
        raise ApprovalError(400, "An application/json request is required.")
    data = bytearray()
    try:
        async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                if len(data) + len(chunk) > 8192:
                    raise ApprovalError(413, "Enrollment request is too large.")
                data.extend(chunk)
    except TimeoutError:
        raise ApprovalError(408, "Enrollment request body timed out.") from None
    try:
        result = _json(data)
        if not isinstance(result, dict) or set(result) != set(keys):
            raise ValueError()
        return result
    except Exception:
        raise ApprovalError(400, "Invalid enrollment fields.") from None


async def warehouse_request(request):
    request_id = uuid4()
    suffix = request.url.path.rsplit("/", 1)[-1]
    try:
        context = (
            await revocation_context(request.headers.get('authorization'), request.path_params.get('subject'))
            if request.method == 'DELETE' or suffix == 'release' else
            await native_context(request.headers.get('authorization'), request.path_params.get('subject'), request.method != 'GET')
        )
        actor, subject = context["actor_id"], context["target_id"]
        service = configured_service()
        ref = context["warehouse"]
        username = ref["username"] if ref else ""
        decision = await store.status(actor, subject, username)
        if request.method == "DELETE":
            await body(request, ())
            decision = await store.revoke(actor, subject, request_id)
        elif suffix == "release":
            await body(request, ())
            decision = await store.release(actor, subject, request_id)
        elif request.method != "GET":
            if not ref or ref["service"] != urlsplit(service).hostname:
                raise ApprovalError(
                    409, "Set the reference to the configured warehouse service first."
                )
            if suffix == "prepare":
                await body(request, ())
                decision = await store.prepare(
                    actor, subject, username, service, request_id
                )
            else:
                if not public_status(decision, context, service)["reference_matches"]:
                    raise ApprovalError(
                        409, "The stored binding and platform reference differ."
                    )
                if suffix == "credentials":
                    data = await body(request, ("password",))
                    password = data.pop("password")
                    if (
                        not isinstance(password, str)
                        or not password
                        or "\x00" in password
                        or len(password.encode()) > 4096
                    ):
                        raise ApprovalError(400, "Invalid credential input.")
                    if decision["state"] not in ("ready", "active"):
                        raise ApprovalError(
                            409, "Prepare the warehouse mapping before enrollment."
                        )
                    keys = await asyncio.to_thread(
                        load_keyring, os.getenv("KAI_BINDING_KEY_DIR", "")
                    )
                    version = int(os.getenv("KAI_BINDING_ACTIVE_KEY_VERSION", "1"))
                    binding = BindingContext(
                        UUID(decision["principal_id"]),
                        username,
                        version,
                        UUID(decision["binding_id"]),
                        service,
                    )
                    cipher = encrypt_password(password, binding, keys)
                    fresh = await native_context(
                        request.headers.get("authorization"), subject, True
                    )
                    if fresh != context:
                        raise ApprovalError(
                            409, "Platform account changed during verification."
                        )
                    await verify_clickhouse(username, password, request_id)
                    del password
                    # Native identity/reference and DB revision are rechecked after remote verification.
                    fresh = await native_context(
                        request.headers.get("authorization"), subject, True
                    )
                    if fresh != context:
                        raise ApprovalError(
                            409, "Platform account changed during verification."
                        )
                    decision = await store.save(
                        actor,
                        subject,
                        username,
                        service,
                        binding.binding_id,
                        UUID(decision["revision"]),
                        cipher,
                        version,
                        request_id,
                    )
                else:
                    raise ApprovalError(400, "Invalid warehouse action.")
        result = public_status(decision, context, service)
        status_code = 200
    except ApprovalError as error:
        status_code, result = error.status, {"message": str(error)}
    except Exception:
        status_code, result = 503, {"message": "Warehouse enrollment is unavailable."}
    return JSONResponse(
        result, status_code=status_code, headers={"Cache-Control": "no-store"}
    )


async def warehouse_eligibility(request):
    """Admin-only fresh eligibility receipt; no credentials or Cloud requests."""
    try:
        context = await native_context(request.headers.get('authorization'), request.path_params.get('subject'), False)
        service = configured_service()
        ref = context['warehouse']
        decision = await store.status(context['actor_id'], context['target_id'], ref['username'] if ref else '')
        status = public_status(decision, context, service)
        eligible = status['state'] == 'active' and status['credentials_saved'] and status['reference_matches']
        result = {'version': 1, 'eligible': eligible, 'binding_id': str(UUID(decision['binding_id'])) if eligible else None, 'revision': str(UUID(decision['revision'])) if eligible else None}
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})
    except ApprovalError as error:
        return JSONResponse({'message': str(error)}, status_code=error.status, headers={'Cache-Control': 'no-store'})
    except Exception:
        return JSONResponse({'message': 'Warehouse eligibility is unavailable.'}, status_code=503, headers={'Cache-Control': 'no-store'})


async def ping_clickhouse():
    """Reach the configured ClickHouse over verified HTTPS without credentials (GET /ping)."""
    service = configured_service()
    try:
        async with asyncio.timeout(6):
            async with httpx.AsyncClient(timeout=3, trust_env=False, follow_redirects=False, verify=_tls()) as client:
                response = await client.get(service + "/ping")
    except (ssl.SSLError, httpx.ConnectError) as error:
        if isinstance(error, ssl.SSLError) or "CERTIFICATE" in str(error).upper():
            raise ApprovalError(400, "Sertifikat TLS server ClickHouse tidak dipercaya. Periksa CLICKHOUSE_CA_CERT untuk CA privat.") from None
        raise ApprovalError(400, "Server ClickHouse tidak bisa dihubungi dari server K-AI. Periksa host, port, dan izin jaringan keluar (IP allowlist ClickHouse).") from None
    except (TimeoutError, httpx.TimeoutException):
        raise ApprovalError(400, "Koneksi ke server ClickHouse habis waktu. Biasanya IP server K-AI belum di-allowlist di ClickHouse.") from None
    except Exception:
        raise ApprovalError(400, "Server ClickHouse tidak bisa dihubungi dari server K-AI.") from None
    if response.status_code != 200:
        raise ApprovalError(400, f"Server ClickHouse menjawab HTTP {response.status_code}. Periksa alamat dan port HTTPS ClickHouse.")


async def warehouse_check(request):
    """Admin-triggered tests that store nothing: 'connection' (no credentials) or 'credentials' (login + read-only)."""
    try:
        await native_context(request.headers.get("authorization"), request.path_params.get("subject"), True)
        service = configured_service()
        target = urlsplit(service)
        if request.url.path.endswith("/connection"):
            await body(request, ())
            await ping_clickhouse()
            message = f"Server ClickHouse {target.hostname}:{target.port} bisa dijangkau."
        else:
            data = await body(request, ("username", "password"))
            username, password = data.pop("username"), data.pop("password")
            if (not isinstance(username, str) or not username or username != username.strip() or len(username.encode()) > 1024
                    or any(ord(c) < 32 or ord(c) == 127 for c in username) or not isinstance(password, str) or not password
                    or "\x00" in password or len(password.encode()) > 4096):
                raise ApprovalError(400, "Periksa username dan password ClickHouse.")
            await verify_clickhouse(username, password, uuid4())
            del password
            message = f"Login {username} berhasil dan akunnya read-only. Kredensial belum disimpan."
        return JSONResponse({"ok": True, "message": message}, headers={"Cache-Control": "no-store"})
    except ApprovalError as error:
        if error.status == 400:
            text = {"ClickHouse login or access was rejected.": "ClickHouse menolak username atau password.",
                    "ClickHouse identity or read-only verification failed.": "Login berhasil, tetapi akun ini tidak read-only atau identitasnya tidak cocok. Minta DBA mengatur akun read-only.",
                    "Invalid enrollment fields.": "Periksa username dan password ClickHouse."}.get(str(error), str(error))
            return JSONResponse({"ok": False, "message": text}, headers={"Cache-Control": "no-store"})
        return JSONResponse({"message": str(error)}, status_code=error.status, headers={"Cache-Control": "no-store"})
    except Exception:
        return JSONResponse({"message": "Warehouse check is unavailable."}, status_code=503, headers={"Cache-Control": "no-store"})


def register_warehouse_routes(mcp):
    root = "/v1/admin/warehouse-users/{subject}"
    mcp.custom_route(root, methods=["GET", "DELETE"])(warehouse_request)
    mcp.custom_route(root + '/eligibility', methods=['GET'])(warehouse_eligibility)
    for check in ("connection", "credentials"):
        mcp.custom_route(root + "/check/" + check, methods=["POST"])(warehouse_check)
    for action in ("prepare", "credentials", "release"):
        mcp.custom_route(root + "/" + action, methods=["POST"])(warehouse_request)
