"""Create local development secrets once; never rotate an existing environment."""

import os
from pathlib import Path
import secrets
import re
import stat
import tempfile
import fcntl
from contextlib import contextmanager
import argparse
import ipaddress


@contextmanager
def environment_lock(runtime: Path):
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(runtime / '.env.setup.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('Unsafe setup lock; inspect ownership and permissions privately.')
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def settings(text):
    result = {}
    for line in text.splitlines():
        match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$', line)
        if not match and line.strip() and not line.lstrip().startswith('#'):
            raise ValueError('Unsupported environment line; resolve privately before migration.')
        if match:
            if match[1] in result:
                raise ValueError('Duplicate environment setting; resolve privately.')
            result[match[1]] = match[2].strip()
    return result


def value(raw):
    raw = raw.strip()
    if raw.startswith(('"', "'")):
        quote = raw[0]
        end = raw.rfind(quote)
        if end == 0 or (raw[end + 1:].strip() and not raw[end + 1:].strip().startswith('#')):
            raise ValueError('Invalid quoted environment setting; inspect privately.')
        return raw[1:end]
    if raw.startswith('#'):
        return ''
    return re.split(r'\s+#', raw, maxsplit=1)[0].strip()


def patch_settings(text, updates):
    lines = text.splitlines(keepends=True)
    remaining = dict(updates)
    for i, line in enumerate(lines):
        match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
        if match and match[1] in remaining:
            lines[i] = match[1] + '=' + remaining.pop(match[1]) + '\n'
    if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'
    lines.extend(key + '=' + raw + '\n' for key, raw in remaining.items())
    return ''.join(lines)


def write_environment(path, text):
    target = path.resolve() if path.is_symlink() else path
    if not target.is_file() or not stat.S_ISREG(target.stat().st_mode):
        raise ValueError('Environment target must be a regular file.')
    descriptor, name = tempfile.mkstemp(prefix='.kai-env-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def mcp_enabled(fields):
    state = value(fields.get('KAI_MCP_ENABLED', 'false')).lower()
    if state not in ('true', 'false'):
        raise ValueError('KAI_MCP_ENABLED must be true or false.')
    return state == 'true'


def mcp_overlays(fields):
    local_metadata = value(fields.get('KAI_LOCAL_METADATA_ENABLED', 'false')).lower()
    if local_metadata not in ('true', 'false'):
        raise ValueError('KAI_LOCAL_METADATA_ENABLED must be true or false.')
    if not mcp_enabled(fields):
        if local_metadata == 'true':
            raise ValueError('Local metadata requires MCP to be enabled.')
        return ''
    overlays = '-f deploy/docker-compose.mcp.yml'
    source = value(fields.get('KAI_BINDING_KEY_SOURCE', ''))
    if local_metadata == 'true':
        if source:
            raise ValueError('Choose local metadata key volume or external host keyring.')
        return overlays + ' -f deploy/docker-compose.metadata-local.yml'
    if source:
        if not Path(source).is_absolute() or any(ord(c) < 32 for c in source):
            raise ValueError('Keyring source must be an absolute host directory.')
        overlays += ' -f deploy/docker-compose.bindings.yml'
    return overlays


def vertex_overlay(fields):
    """Gemini on Vertex AI: mount the service account file only when one is configured."""
    source = value(fields.get('KAI_VERTEX_KEY_FILE', ''))
    if not source:
        return ''
    if not Path(source).is_absolute() or any(ord(c) < 32 or c in ' :' for c in source):
        raise ValueError('KAI_VERTEX_KEY_FILE must be an absolute host path without spaces or colons.')
    return ' -f deploy/docker-compose.vertex.yml'


def tls_overlay(fields):
    """HTTPS entrypoint (Caddy on 443) whenever KAI_DOMAIN is set: a bare hostname, or an IPv4 address
    (Caddy then serves its own certificate; browsers warn until a real domain is used)."""
    domain = value(fields.get('KAI_DOMAIN', ''))
    if not domain:
        return ''
    try:
        ipaddress.IPv4Address(domain)
    except ValueError:
        if len(domain) > 253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                                    for label in domain.split('.')):
            raise ValueError('KAI_DOMAIN must be a bare hostname or IPv4 address.') from None
    return '-f deploy/docker-compose.tls.yml'


def validate_mcp_target(host, port):
    if (not host or len(host) > 253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in host.split('.'))
            or type(port) is not int or not 1 <= port <= 65535):
        raise ValueError('Provide a hostname without URL/path and a port from 1 to 65535.')


def migrate_auxiliary(runtime):
    path = runtime / '.env'
    text = path.read_text()
    fields = settings(text)
    updates, sources = {}, []
    allowed_mcp = {'CLICKHOUSE_HOST', 'CLICKHOUSE_PORT', 'CLICKHOUSE_SECURE', 'CLICKHOUSE_VERIFY', 'CLICKHOUSE_USER',
                   'CLICKHOUSE_PASSWORD', 'CLICKHOUSE_MCP_AUTH_TOKEN', 'KAI_MCP_ENABLED', 'KAI_BINDING_MODE',
                   'KAI_APPROVAL_DSN', 'KAI_BINDING_DSN', 'KAI_BINDING_KEY_DIR', 'KAI_LIBRECHAT_INTERNAL_URL', 'KAI_LIBRECHAT_ISSUER'}
    for filename, allowed in [('mcp.env', allowed_mcp), ('metrics.env', {'KAI_METRICS_TOKEN'})]:
        source = runtime / 'deploy/runtime' / filename
        if not source.exists() and not source.is_symlink():
            continue
        if source.is_symlink():
            raise ValueError('Legacy auxiliary configuration cannot be a symlink.')
        info = source.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise ValueError('Legacy auxiliary configuration must be a private owned regular file.')
        legacy = settings(source.read_text())
        if not legacy or set(legacy) - allowed:
            raise ValueError('Unexpected legacy auxiliary settings; inspect privately.')
        for key, raw in legacy.items():
            if value(fields.get(key, '')) and value(fields[key]) != value(raw):
                raise ValueError('Root and legacy auxiliary values conflict; resolve privately before setup.')
            if not value(fields.get(key, '')):
                updates[key] = raw
        if filename == 'mcp.env':
            updates['KAI_MCP_ENABLED'] = 'true'
        sources.append(source)
    # Validate the merged optional configuration before committing or removing any source.
    if sources:
        updates.update(optional_updates({**fields, **updates}))
    if updates:
        write_environment(path, patch_settings(text, updates))
    for source in sources:
        source.unlink()
    return bool(updates or sources)


def optional_updates(fields):
    defaults = {'KAI_MCP_ENABLED': 'false', 'CLICKHOUSE_HOST': '', 'CLICKHOUSE_PORT': '8443',
                'CLICKHOUSE_SECURE': 'true', 'CLICKHOUSE_VERIFY': 'true', 'CLICKHOUSE_USER': 'kai_unbound',
                'CLICKHOUSE_PASSWORD': '', 'CLICKHOUSE_MCP_AUTH_TOKEN': '', 'KAI_PLATFORM_INTERNAL_TOKEN': '',
                'KAI_METRICS_TOKEN': ''}
    updates = {key: raw for key, raw in defaults.items() if key not in fields}
    combined = {**fields, **updates}
    if mcp_enabled(combined):
        try:
            port = int(value(combined['CLICKHOUSE_PORT']))
        except ValueError:
            raise ValueError('CLICKHOUSE_PORT must be a valid port.') from None
        validate_mcp_target(value(combined['CLICKHOUSE_HOST']), port)
        for key in ('CLICKHOUSE_PASSWORD', 'CLICKHOUSE_MCP_AUTH_TOKEN', 'KAI_PLATFORM_INTERNAL_TOKEN'):
            if not value(combined.get(key, '')):
                updates[key] = secrets.token_hex(32)
    combined = {**combined, **updates}
    tokens = [value(combined.get(key, '')) for key in ('CLICKHOUSE_MCP_AUTH_TOKEN', 'KAI_PLATFORM_INTERNAL_TOKEN')]
    if all(tokens) and tokens[0] == tokens[1]:
        raise ValueError('Transport and platform-internal tokens must differ.')
    metrics = value(combined.get('KAI_METRICS_TOKEN', ''))
    if metrics and (not re.fullmatch('[a-f0-9]{64}', metrics) or metrics in tokens):
        raise ValueError('Metrics token must be a distinct 64-hex secret.')
    return updates


def optional_defaults(runtime):
    path = runtime / '.env'
    text = path.read_text()
    updates = optional_updates(settings(text))
    if updates:
        write_environment(path, patch_settings(text, updates))
    return bool(updates)


def initialize(runtime: Path) -> bool:
    with environment_lock(runtime):
        changed = _initialize(runtime)
        changed = migrate_auxiliary(runtime) or changed
        changed = optional_defaults(runtime) or changed
        return changed


def update_configuration(runtime, transform):
    initialize(runtime)
    with environment_lock(runtime):
        path = runtime / '.env'
        text = path.read_text()
        updates = transform(settings(text))
        if not updates:
            return False
        write_environment(path, patch_settings(text, updates))
        return True


def _initialize(runtime: Path) -> bool:
    environment = runtime / ".env"
    migrated = False
    legacy = runtime / 'deploy' / 'runtime' / '.env'
    if legacy.exists() or legacy.is_symlink():
        if legacy.is_symlink() or not legacy.is_file():
            raise ValueError('Legacy environment must be a regular file.')
        settings(legacy.read_text())
        if environment.exists() or environment.is_symlink():
            if environment.is_symlink() and environment.is_file() and os.path.samefile(environment, legacy):
                return False
            if environment.is_symlink() or environment.read_bytes() != legacy.read_bytes():
                raise ValueError('Root and legacy environments differ; resolve privately before setup.')
            environment.chmod(0o600)
            legacy.unlink()
        else:
            # Hard-link then unlink preserves every byte and cannot replace a concurrent root file.
            os.link(legacy, environment)
            legacy.unlink()
            environment.chmod(0o600)
            migrated = True
    if environment.is_symlink():
        # Ansible links the reviewed release to its protected external runtime.
        if not environment.is_file():
            raise ValueError('Environment link has no regular target.')
        return False
    values = {
        "PORT": "3080",
        "UID": "1000",
        "GID": "1000",
        "DOMAIN_CLIENT": "http://localhost:3080",
        "DOMAIN_SERVER": "http://localhost:3080",
        "APP_TITLE": "K-AI platform",
        "ENDPOINTS": "custom,agents",
        "ALLOW_EMAIL_LOGIN": "true",
        "ALLOW_REGISTRATION": "false",
        "ALLOW_SOCIAL_LOGIN": "false",
        "ALLOW_SOCIAL_REGISTRATION": "false",
        "ALLOW_PASSWORD_RESET": "false",
        "ALLOW_UNVERIFIED_EMAIL_LOGIN": "true",
        "ALLOW_SHARED_LINKS": "false",
        "ALLOW_SHARED_LINKS_PUBLIC": "false",
        "CREDS_KEY": secrets.token_hex(32),
        "CREDS_IV": secrets.token_hex(16),
        "JWT_SECRET": secrets.token_hex(32),
        "JWT_REFRESH_SECRET": secrets.token_hex(32),
        "MEILI_MASTER_KEY": secrets.token_hex(32),
        "MEILI_NO_ANALYTICS": "true",
        "POSTGRES_DB": "kai_rag",
        "POSTGRES_USER": "kai_rag",
        "POSTGRES_PASSWORD": secrets.token_hex(32),
        "OLLAMA_API_KEY": "",
        "OLLAMA_MODEL": "gpt-oss:120b",
        "ADMIN_PANEL_SESSION_SECRET": secrets.token_hex(32),
        # RustFS root credentials for uploaded documents (P6-12); secret <= 40 chars like other S3 servers.
        "KAI_S3_ACCESS_KEY": "kai" + secrets.token_hex(8),
        "KAI_S3_SECRET_KEY": secrets.token_hex(20),
        "KAI_S3_APP_ACCESS_KEY": "kaiapp" + secrets.token_hex(8),
        "KAI_S3_APP_SECRET_KEY": secrets.token_hex(20),
        "ADMIN_PANEL_URL": "http://localhost:3081",
        "DEBUG_LOGGING": "false",
        "DEBUG_CONSOLE": "false",
    }
    if environment.exists():
        if not stat.S_ISREG(environment.stat().st_mode):
            raise ValueError('Environment must be a regular file.')
        text = environment.read_text()
        settings(text)
        lines = text.splitlines(keepends=True)
        secret_names = {'CREDS_KEY', 'CREDS_IV', 'JWT_SECRET', 'JWT_REFRESH_SECRET',
                        'MEILI_MASTER_KEY', 'POSTGRES_PASSWORD', 'ADMIN_PANEL_SESSION_SECRET',
                        'KAI_S3_ACCESS_KEY', 'KAI_S3_SECRET_KEY', 'KAI_S3_APP_ACCESS_KEY', 'KAI_S3_APP_SECRET_KEY'}
        seen = set()
        changed = False
        for index, line in enumerate(lines):
            match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)', line)
            if not match or match[1] not in secret_names:
                continue
            key, raw = match[1], match[2]
            if key in seen:
                raise ValueError('Duplicate generated secret setting; resolve privately before setup.')
            seen.add(key)
            if not value(raw):
                lines[index] = f'{key}={values[key]}\n'
                changed = True
        for key in sorted(secret_names - seen):
            if lines and not lines[-1].endswith('\n'):
                lines[-1] += '\n'
            lines.append(f'{key}={values[key]}\n')
            changed = True
        environment.chmod(0o600)
        if not changed:
            return migrated
        # Write a complete protected file before replacing a copied template.
        descriptor, name = tempfile.mkstemp(prefix='.kai-env-', dir=runtime)
        try:
            with os.fdopen(descriptor, 'w') as output:
                output.write(''.join(lines))
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, environment)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return True
    # Exclusive creation prevents a concurrent setup from replacing encryption keys.
    descriptor = os.open(environment, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write("".join(f"{key}={value}\n" for key, value in values.items()))
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mcp-overlay', action='store_true')
    parser.add_argument('--tls-overlay', action='store_true')
    args = parser.parse_args()
    destination = Path(__file__).resolve().parents[1]
    try:
        if args.tls_overlay:
            fields = settings((destination / '.env').read_text()) if (destination / '.env').exists() else {}
            print(tls_overlay(fields))
        elif args.mcp_overlay:
            fields = settings((destination / '.env').read_text()) if (destination / '.env').exists() else {}
            print((mcp_overlays(fields) + vertex_overlay(fields)).strip())
        else:
            created = initialize(destination)
            print('Unified root environment created or prepared.' if created else 'Existing environment preserved.')
    except (ValueError, OSError):
        parser.exit(1, 'Configuration refused; inspect .env and any legacy files privately. No values printed.\n')
