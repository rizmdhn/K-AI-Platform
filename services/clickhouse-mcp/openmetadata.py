"""OpenMetadata: the client's data catalog and the source of truth for table/column metadata (FR-08/FR-11).

The DBA curates and approves metadata in OpenMetadata's own UI. kAI only
  - reads descriptions and tags for table search (MCP), and
  - sends AI drafts as OpenMetadata *suggestions* for empty descriptions (metadata worker).
Stdlib only, no proxy from the environment, no redirects (the bot token is never forwarded). Errors are fixed
codes; the token is never printed. Check the connection with: make openmetadata-check (runs `python -m openmetadata`).
"""

import asyncio
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 15
PAGE = 100
MAX_TABLES = 5000  # ponytail: one service listing per refresh; page by schema if a catalog outgrows this
CACHE_SECONDS = 600
SUGGESTIONS_SINCE = (1, 3)  # the suggestions API appeared in OpenMetadata 1.3


class OpenMetadataError(Exception):
    """Fixed diagnostics only (HTTP status or error type); never the response body or the token."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class OpenMetadata:
    def __init__(self, url, token, ca_cert=None):
        parts = urllib.parse.urlsplit(url or "")
        if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.query or parts.fragment:
            raise ValueError("KAI_OPENMETADATA_URL must be the OpenMetadata base URL, e.g. https://openmetadata.example.com")
        if not token:
            raise ValueError("KAI_OPENMETADATA_TOKEN is empty.")
        self.base = url.rstrip("/").removesuffix("/api")
        self.insecure = parts.scheme == "http"
        # ca_cert: PEM text (saved in the Admin Panel) or a file path inside the container (.env).
        pem = bool(ca_cert) and "-----BEGIN" in ca_cert
        context = ssl.create_default_context(cadata=ca_cert if pem else None, cafile=None if pem else ca_cert or None)
        self._open = urllib.request.build_opener(
            _NoRedirect, urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)).open
        self._token = token

    def _call(self, method, path, params=None, body=None):
        url = f"{self.base}/api/v1{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, method=method, headers=headers,
                                         data=None if body is None else json.dumps(body).encode())
        try:
            with self._open(request, timeout=TIMEOUT) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            raise OpenMetadataError(f"HTTP {error.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise OpenMetadataError(type(error).__name__) from None

    def version(self):
        return str((self._call("GET", "/system/version") or {}).get("version", ""))

    def tables(self, service):
        after, count = None, 0
        while True:
            params = {"service": service, "fields": "columns,tags", "limit": PAGE}
            if after:
                params["after"] = after
            page = self._call("GET", "/tables", params) or {}
            for table in page.get("data", []):
                yield table
                count += 1
                if count >= MAX_TABLES:
                    return
            after = (page.get("paging") or {}).get("after")
            if not after:
                return

    def suggest(self, entity_link, description):
        self._call("POST", "/suggestions", body={"type": "SuggestDescription", "entityLink": entity_link,
                                                  "description": description})


def from_env(env=os.environ):
    """Configured client and service name, or None when OpenMetadata is not configured."""
    if not env.get("KAI_OPENMETADATA_URL", "").strip():
        return None
    service = env.get("KAI_OPENMETADATA_SERVICE", "").strip()
    if not service:
        raise ValueError("KAI_OPENMETADATA_SERVICE (the ClickHouse service name in OpenMetadata) is empty.")
    client = OpenMetadata(env["KAI_OPENMETADATA_URL"].strip(), env.get("KAI_OPENMETADATA_TOKEN", "").strip(),
                          env.get("KAI_OPENMETADATA_CA_CERT", "").strip() or None)
    return client, service


def key(table):
    """(ClickHouse database, table): OpenMetadata maps a ClickHouse database to a database schema."""
    return (table.get("databaseSchema") or {}).get("name"), table.get("name")


def _quote(name):
    return f'"{name}"' if "." in name else name


def entity_link(fqn, column=None):
    return f"<#E::table::{fqn}" + (f"::columns::{_quote(column)}" if column else "") + ">"


def supports_suggestions(version):
    try:
        return tuple(int(p) for p in version.split(".")[:2]) >= SUGGESTIONS_SINCE
    except ValueError:
        return False


def view(table):
    """What search may show: curated description, column descriptions and tag/glossary names."""
    columns = {c["name"]: c["description"].strip() for c in table.get("columns") or []
               if isinstance(c.get("name"), str) and isinstance(c.get("description"), str) and c["description"].strip()}
    tags = sorted({t["tagFQN"] for t in table.get("tags") or [] if isinstance(t.get("tagFQN"), str)})
    description = table.get("description") if isinstance(table.get("description"), str) else ""
    result = {k: v for k, v in (("description", description.strip()), ("columns", columns), ("tags", tags)) if v}
    return result or None


_cache = {"at": -CACHE_SECONDS, "data": {}}


def reset_cache():
    """Called after the Admin Panel saves new settings, so search uses them right away."""
    _cache.update(at=-CACHE_SECONDS, data={})


async def approved_metadata():
    """{(database, table): metadata} from OpenMetadata, cached 10 minutes; {} when not configured.

    Settings come from the Admin Panel when saved there, otherwise .env. On an outage the last good copy is
    kept, so search degrades to ClickHouse comments only, never fails.
    """
    if time.monotonic() - _cache["at"] < CACHE_SECONDS:
        return _cache["data"]
    try:
        import integration_settings  # lazy: it imports this module

        configured = from_env(await asyncio.to_thread(integration_settings.effective))
    except Exception:
        configured = None
    if not configured:
        _cache.update(at=time.monotonic(), data={})
        return {}
    client, service = configured
    try:
        data = await asyncio.to_thread(lambda: {key(t): v for t in client.tables(service) if (v := view(t))})
    except Exception:
        data = _cache["data"]
    _cache.update(at=time.monotonic(), data=data)
    return data


def database_summary(tables):
    """[{name, tables, empty_tables, empty_columns}] per ClickHouse database, for the Admin Panel's database picker."""
    result = {}
    for table in tables:
        name = key(table)[0]
        if not name:
            continue
        row = result.setdefault(name, {"name": name, "tables": 0, "empty_tables": 0, "empty_columns": 0})
        row["tables"] += 1
        row["empty_tables"] += not (table.get("description") or "").strip()
        row["empty_columns"] += sum(1 for c in table.get("columns") or [] if not (c.get("description") or "").strip())
    return sorted(result.values(), key=lambda row: row["name"])


def check(env=os.environ, out=print, found=None):
    """Connection check for operators: version, suggestions support, table counts. Never prints the token.

    found: optional list that receives the tables seen (the Admin Panel builds its database list from it)."""
    try:
        configured = from_env(env)
        if not configured:
            out("OpenMetadata not configured (KAI_OPENMETADATA_URL is empty).")
            return 1
        client, service = configured
        if client.insecure:
            out("WARNING: http:// sends the bot token unencrypted; use https:// outside a private network.")
        version = client.version()
        out(f"Connected: OpenMetadata {version or 'unknown version'}")
        out("Suggestions API: " + ("available" if supports_suggestions(version) else
            "NOT available (needs 1.3+); the worker cannot send drafts, search still reads descriptions"))
        tables = list(client.tables(service))
        if found is not None:
            found.extend(tables)
    except (ValueError, OpenMetadataError) as error:
        out(f"OpenMetadata check FAILED: {error}")
        return 1
    if not tables:
        out(f"Service '{service}': no tables visible to this token (wrong service name or missing permission).")
        return 1
    databases = sorted({key(t)[0] for t in tables if key(t)[0]})
    empty = sum(1 for t in tables if not (t.get("description") or "").strip())
    columns = [c for t in tables for c in t.get("columns") or []]
    missing = sum(1 for c in columns if not (c.get("description") or "").strip())
    out(f"Service '{service}': {len(tables)} table(s) in {len(databases)} database(s); "
        f"{empty} table(s) and {missing} of {len(columns)} column(s) without a description.")
    wanted = {d.strip() for d in env.get("KAI_CATALOG_DATABASES", "").split(",") if d.strip()}
    if wanted - set(databases):
        out("KAI_CATALOG_DATABASES lists database(s) not in this service: " + ", ".join(sorted(wanted - set(databases))))
    out("Write permission for suggestions is only proven by the first worker run (this check sends nothing).")
    return 0


if __name__ == "__main__":
    sys.exit(check())
