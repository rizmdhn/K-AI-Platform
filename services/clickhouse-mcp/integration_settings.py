"""Settings edited in the Admin Panel, stored in PostgreSQL; .env is only the fallback until a section is saved.

Sections (one row each in kai.integration_settings, kai_catalog role, KAI_CATALOG_DSN):
  openmetadata  OpenMetadata connection + catalog draft worker (bot token secret)
  batch_model   model for bulk row labelling and metadata drafts (API key secret)
  limits        ClickHouse query ceilings + audit log retention
Secrets are AES-256-GCM encrypted with the binding keyring and never returned: the panel only learns whether
one is saved. Values are exposed as an env-style dict, so code reads UI and .env values the same way.
Internal routes take the platform internal token only.
"""

import asyncio
import hmac
import json
import os
import re
import time
import urllib.parse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from starlette.responses import JSONResponse

import openmetadata
from credential_crypto import load_keyring

NAME = "openmetadata"
SECRET = "KAI_OPENMETADATA_TOKEN"
FIELDS = ("KAI_OPENMETADATA_URL", "KAI_OPENMETADATA_SERVICE", "KAI_OPENMETADATA_CA_CERT", SECRET,
          "KAI_CATALOG_ENABLED", "KAI_CATALOG_DATABASES", "KAI_CATALOG_SCAN_HOURS", "KAI_CATALOG_MAX_TABLES_PER_SCAN")
DATABASE = re.compile(r"[A-Za-z0-9_.-]{1,128}")


class SettingsError(ValueError):
    """Message is safe to show to the admin (fixed text, never a value)."""


def _connect(env):
    import psycopg

    return psycopg.connect(env["KAI_CATALOG_DSN"], connect_timeout=3, autocommit=True,
                           options="-c statement_timeout=3000", application_name="kai-integration-settings")


def read_row(env=os.environ, name=NAME):
    """(config, secret, key_version, updated_by, updated_at) or None; None also when no catalog DSN."""
    if not env.get("KAI_CATALOG_DSN"):
        return None
    with _connect(env) as connection:
        return connection.execute(
            "SELECT config, secret, key_version, updated_by, updated_at FROM kai.integration_settings WHERE name=%s",
            (name,)).fetchone()


def _aad(name):
    return f'["kai.integration-setting.v1","{name}"]'.encode()


def _keys(env):
    return load_keyring(env.get("KAI_BINDING_KEY_DIR", "/run/kai-binding-keys"))


def _decrypt(secret, version, env, name=NAME):
    blob = bytes(secret)
    return AESGCM(_keys(env)[version]).decrypt(blob[:12], blob[12:], _aad(name)).decode()


def effective(env=os.environ, row=False, name=NAME):
    """Env-style settings of one section: Admin Panel values once saved, otherwise .env. Adds 'source'."""
    section = SECTIONS[name]
    row = read_row(env, name) if row is False else row
    if not row:
        base = {key: env.get(key, "") for key in section["fields"]}
        marker = section["marker"]
        return {**base, "source": "env" if not marker or base[marker].strip() else "none"}
    config, secret, version = row[0], row[1], row[2]
    result = {key: str(config.get(key, "")) for key in section["fields"] if key != section["secret"]}
    if section["secret"]:
        result[section["secret"]] = _decrypt(secret, version, env, name) if secret else ""
    return {**result, "source": "ui"}


_current = {"at": -60.0, "values": {}}
CURRENT_SECONDS = 60


def current(env=os.environ):
    """Every section as one env-style dict (Admin Panel over .env), for the code that uses the settings.

    .env is read live; saved sections are cached 60 s (cleared on save). If PostgreSQL is unreachable the
    last good values are kept, so a database blip never drops a saved setting back to .env.
    """
    values = {key: env.get(key, "") for section in SECTIONS.values() for key in section["fields"]}
    if not env.get("KAI_CATALOG_DSN"):
        return values
    if time.monotonic() - _current["at"] >= CURRENT_SECONDS:
        try:
            saved = {}
            for name in SECTIONS:
                row = read_row(env, name)
                if row:
                    saved.update({k: v for k, v in effective(env, row, name).items() if k != "source"})
            _current["values"] = saved
        except Exception:
            pass  # keep the last good copy
        _current["at"] = time.monotonic()
    return {**values, **_current["values"]}


def reset_current():
    _current.update(at=-float(CURRENT_SECONDS))


def _int(value, low, high, name):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{name} harus angka {low}-{high}.") from None
    if not low <= number <= high:
        raise SettingsError(f"{name} harus angka {low}-{high}.")
    return number


def clean(body, current):
    """OpenMetadata form -> validated env-style settings; an empty token keeps the saved one."""
    if not isinstance(body, dict):
        raise SettingsError("Isian tidak valid.")
    text = lambda key, limit: (body.get(key) if isinstance(body.get(key), str) else "").strip()[:limit]
    url, service, ca = text("url", 2048), text("service", 256), text("ca_cert", 65536)
    token = text("token", 8192) or current.get(SECRET, "")
    databases = body.get("databases") if isinstance(body.get("databases"), list) else []
    if len(databases) > 100 or not all(isinstance(d, str) and DATABASE.fullmatch(d) for d in databases):
        raise SettingsError("Daftar database hanya boleh berisi nama database (huruf, angka, _ . -).")
    enabled = body.get("catalog_enabled") is True
    if url:
        if not service:
            raise SettingsError("Isi nama service ClickHouse di OpenMetadata.")
        if not token:
            raise SettingsError("Isi token bot OpenMetadata.")
        if ca and "-----BEGIN CERTIFICATE-----" not in ca:
            raise SettingsError("CA harus berupa sertifikat PEM (-----BEGIN CERTIFICATE-----).")
        try:
            openmetadata.OpenMetadata(url, token, ca or None)
        except ValueError:
            raise SettingsError("URL harus alamat dasar OpenMetadata, mis. https://openmetadata.perusahaan.co.id") from None
        except Exception:
            raise SettingsError("Sertifikat CA tidak bisa dibaca.") from None
    elif enabled:
        raise SettingsError("Draf metadata butuh koneksi OpenMetadata. Isi URL terlebih dahulu.")
    return {
        "KAI_OPENMETADATA_URL": url, "KAI_OPENMETADATA_SERVICE": service, "KAI_OPENMETADATA_CA_CERT": ca,
        SECRET: token if url else "", "KAI_CATALOG_ENABLED": "true" if enabled else "false",
        "KAI_CATALOG_DATABASES": ",".join(sorted(set(databases))),
        "KAI_CATALOG_SCAN_HOURS": str(_int(body.get("scan_hours", 24), 1, 168, "Interval scan (jam)")),
        "KAI_CATALOG_MAX_TABLES_PER_SCAN": str(_int(body.get("max_tables", 50), 1, 1000, "Batas tabel per scan")),
    }


def save(settings, updated_by, env=os.environ, name=NAME):
    secret_key = SECTIONS[name]["secret"]
    token = settings.get(secret_key, "") if secret_key else ""
    version = int(env.get("KAI_BINDING_ACTIVE_KEY_VERSION") or 1)
    secret = None
    if token:
        nonce = os.urandom(12)
        secret = nonce + AESGCM(_keys(env)[version]).encrypt(nonce, token.encode(), _aad(name))
    config = {key: value for key, value in settings.items() if key != secret_key}
    with _connect(env) as connection:
        connection.execute(
            """INSERT INTO kai.integration_settings(name,config,secret,key_version,updated_by,updated_at)
            VALUES(%s,%s::jsonb,%s,%s,%s,clock_timestamp())
            ON CONFLICT(name) DO UPDATE SET config=EXCLUDED.config, secret=EXCLUDED.secret,
            key_version=EXCLUDED.key_version, updated_by=EXCLUDED.updated_by, updated_at=EXCLUDED.updated_at""",
            (name, json.dumps(config), secret, version if secret else None, updated_by))


def _meta(settings, row, secret_set=None):
    result = {"source": settings["source"], "storage_available": bool(os.environ.get("KAI_CATALOG_DSN")),
              "updated_at": row[4].isoformat() if row else None, "updated_by": row[3] if row else None}
    return result if secret_set is None else {**result, "secret_set": secret_set}


def public(settings, row):
    """What the panel may see: everything except the token."""
    return {
        "source": settings["source"],
        "settings": {
            "url": settings["KAI_OPENMETADATA_URL"], "service": settings["KAI_OPENMETADATA_SERVICE"],
            "ca_cert": settings["KAI_OPENMETADATA_CA_CERT"],
            "catalog_enabled": settings["KAI_CATALOG_ENABLED"].strip().lower() == "true",
            "databases": [d for d in settings["KAI_CATALOG_DATABASES"].split(",") if d.strip()],
            "scan_hours": int(settings["KAI_CATALOG_SCAN_HOURS"] or 24),
            "max_tables": int(settings["KAI_CATALOG_MAX_TABLES_PER_SCAN"] or 50),
        },
        "token_set": bool(settings[SECRET]),
        "storage_available": bool(os.environ.get("KAI_CATALOG_DSN")),
        "updated_at": row[4].isoformat() if row else None,
        "updated_by": row[3] if row else None,
    }


def _number(text, default):
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


DEFAULT_MODEL_URL = "https://ollama.com/v1"
MODEL_NAME = re.compile(r"[A-Za-z0-9._:/@-]{1,200}")


def clean_batch(body, current):
    """Batch model form: endpoint, model, API key (empty keeps the saved one), labelling on/off and limits."""
    if not isinstance(body, dict):
        raise SettingsError("Isian tidak valid.")
    text = lambda key, limit: (body.get(key) if isinstance(body.get(key), str) else "").strip()[:limit]
    url, model = text("base_url", 2048) or DEFAULT_MODEL_URL, text("model", 200)
    key = text("api_key", 8192) or current.get("KAI_BATCH_MODEL_API_KEY", "")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.query or parts.fragment:
        raise SettingsError("Alamat model harus URL API yang kompatibel OpenAI, mis. https://ollama.com/v1")
    if model and not MODEL_NAME.fullmatch(model):
        raise SettingsError("Nama model hanya boleh huruf, angka, dan . _ : / @ -")
    enabled = body.get("enabled") is True
    if enabled and not (model and key):
        raise SettingsError("Pelabelan massal butuh nama model dan API key.")
    return {
        "KAI_BATCH_ENABLED": "true" if enabled else "false", "KAI_BATCH_MODEL_BASE_URL": url.rstrip("/"),
        "KAI_BATCH_MODEL": model, "KAI_BATCH_MODEL_API_KEY": key,
        "KAI_BATCH_JOBS_PER_DAY": str(_int(body.get("jobs_per_day", 10), 1, 1000, "Batas job per hari")),
        "KAI_BATCH_SAMPLE_ROWS": str(_int(body.get("sample_rows", 2000), 100, 5000, "Ukuran sampel")),
    }


def public_batch(settings, row):
    return {**_meta(settings, row, bool(settings["KAI_BATCH_MODEL_API_KEY"])), "settings": {
        "enabled": settings["KAI_BATCH_ENABLED"].strip().lower() == "true",
        "base_url": settings["KAI_BATCH_MODEL_BASE_URL"] or DEFAULT_MODEL_URL, "model": settings["KAI_BATCH_MODEL"],
        "jobs_per_day": _number(settings["KAI_BATCH_JOBS_PER_DAY"], 10),
        "sample_rows": _number(settings["KAI_BATCH_SAMPLE_ROWS"], 2000)}}


GB, MILLION = 1024**3, 10**6
LIMIT_DEFAULTS = {"KAI_CH_MAX_EXECUTION_TIME": 25, "KAI_CH_MAX_MEMORY_USAGE": 4 * GB,
                  "KAI_CH_MAX_ROWS_TO_READ": 1000 * MILLION, "KAI_CH_MAX_BYTES_TO_READ": 100 * GB,
                  "KAI_LOG_RETENTION_DAYS": 90}
# form field -> (env key, unit, low, high, label)
LIMIT_FORM = {
    "max_execution_seconds": ("KAI_CH_MAX_EXECUTION_TIME", 1, 1, 29, "Batas waktu query (detik)"),
    "max_memory_gb": ("KAI_CH_MAX_MEMORY_USAGE", GB, 1, 1024, "Batas memori per query (GB)"),
    "max_rows_million": ("KAI_CH_MAX_ROWS_TO_READ", MILLION, 1, 1_000_000, "Batas baris dibaca (juta)"),
    "max_read_gb": ("KAI_CH_MAX_BYTES_TO_READ", GB, 1, 100_000, "Batas data dibaca (GB)"),
    "log_retention_days": ("KAI_LOG_RETENTION_DAYS", 1, 1, 3650, "Simpan log audit (hari)"),
}


def limit(values, key):
    """Effective numeric limit from an env-style dict; the default when empty or invalid."""
    return _number(values.get(key), LIMIT_DEFAULTS[key]) or LIMIT_DEFAULTS[key]


def clean_limits(body, current):
    if not isinstance(body, dict):
        raise SettingsError("Isian tidak valid.")
    return {key: str(_int(body.get(field, LIMIT_DEFAULTS[key] // unit), low, high, label) * unit)
            for field, (key, unit, low, high, label) in LIMIT_FORM.items()}


def public_limits(settings, row):
    return {**_meta(settings, row), "settings": {
        field: limit(settings, key) // unit for field, (key, unit, *_rest) in LIMIT_FORM.items()}}


SECTIONS = {
    NAME: {"fields": FIELDS, "secret": SECRET, "marker": "KAI_OPENMETADATA_URL", "clean": clean, "public": public},
    "batch_model": {"fields": ("KAI_BATCH_ENABLED", "KAI_BATCH_MODEL_BASE_URL", "KAI_BATCH_MODEL", "KAI_BATCH_MODEL_API_KEY",
                               "KAI_BATCH_JOBS_PER_DAY", "KAI_BATCH_SAMPLE_ROWS"),
                    "secret": "KAI_BATCH_MODEL_API_KEY", "marker": "KAI_BATCH_MODEL", "clean": clean_batch,
                    "public": public_batch},
    "limits": {"fields": tuple(LIMIT_DEFAULTS), "secret": None, "marker": None, "clean": clean_limits,
               "public": public_limits},
}


def _authorized(request):
    token = os.getenv("KAI_PLATFORM_INTERNAL_TOKEN", "")
    given = request.headers.get("authorization", "")
    return bool(token) and hmac.compare_digest(given.encode(), f"Bearer {token}".encode())


async def _body(request):
    raw = await request.body()
    if len(raw) > 100_000:
        raise SettingsError("Isian terlalu besar.")
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        raise SettingsError("Isian tidak valid.") from None


async def settings_request(request):
    if not _authorized(request):
        return JSONResponse({"message": "Unauthorized."}, status_code=401)
    name = (getattr(request, "path_params", None) or {}).get("name", NAME)
    if name not in SECTIONS:
        return JSONResponse({"message": "Not found."}, status_code=404)
    section = SECTIONS[name]
    try:
        if request.method == "PUT":
            body = await _body(request)
            updated_by = body.get("updated_by")
            if not isinstance(updated_by, str) or not re.fullmatch(r"[0-9a-f]{24}", updated_by):
                raise SettingsError("Isian tidak valid.")
            if not os.environ.get("KAI_CATALOG_DSN"):
                raise SettingsError("Penyimpanan pengaturan belum disiapkan. Jalankan make warehouse-local-setup di server.")
            current_values = await asyncio.to_thread(effective, os.environ, False, name)
            await asyncio.to_thread(save, section["clean"](body, current_values), updated_by, os.environ, name)
            reset_current()
            openmetadata.reset_cache()
        row = await asyncio.to_thread(read_row, os.environ, name)
        return JSONResponse(section["public"](effective(row=row, name=name), row), headers={"Cache-Control": "no-store"})
    except SettingsError as error:
        return JSONResponse({"message": str(error)}, status_code=400)
    except Exception:
        return JSONResponse({"message": "Pengaturan integrasi tidak bisa dibaca atau disimpan."}, status_code=503)


async def test_request(request):
    """Connection check with the form values (saved token when the field is empty). Sends nothing to OpenMetadata."""
    if not _authorized(request):
        return JSONResponse({"message": "Unauthorized."}, status_code=401)
    try:
        current = await asyncio.to_thread(effective)
        settings = clean(await _body(request), current)
        if not settings["KAI_OPENMETADATA_URL"]:
            raise SettingsError("Isi URL OpenMetadata terlebih dahulu.")
        lines, tables = [], []
        code = await asyncio.to_thread(openmetadata.check, settings, lines.append, tables)
        return JSONResponse({"ok": code == 0, "lines": lines, "databases": openmetadata.database_summary(tables)},
                            headers={"Cache-Control": "no-store"})
    except SettingsError as error:
        return JSONResponse({"message": str(error)}, status_code=400)
    except Exception:
        return JSONResponse({"message": "Uji koneksi tidak bisa dijalankan."}, status_code=503)


async def databases_request(request):
    """Databases in the saved OpenMetadata service with description gaps; [] when not configured."""
    if not _authorized(request):
        return JSONResponse({"message": "Unauthorized."}, status_code=401)
    try:
        configured = openmetadata.from_env(await asyncio.to_thread(effective))
        tables = await asyncio.to_thread(lambda: list(configured[0].tables(configured[1]))) if configured else []
        return JSONResponse({"databases": openmetadata.database_summary(tables)}, headers={"Cache-Control": "no-store"})
    except Exception:
        return JSONResponse({"message": "Daftar database dari OpenMetadata tidak bisa dibaca."}, status_code=503)


def register_integration_routes(mcp):
    mcp.custom_route("/v1/internal/integrations/openmetadata/test", methods=["POST"])(test_request)
    mcp.custom_route("/v1/internal/integrations/openmetadata/databases", methods=["GET"])(databases_request)
    mcp.custom_route("/v1/internal/integrations/{name}", methods=["GET", "PUT"])(settings_request)
