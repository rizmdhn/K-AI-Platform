"""Opt-in local Docker metadata provisioning; never restarts API/MCP or enrolls users."""
import argparse
import inspect
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess

import bootstrap

RUNTIME_ROLES = {
    'KAI_BINDING_DSN': 'kai_binding_runtime',
    'KAI_ENROLLMENT_DSN': 'kai_enrollment_runtime',
}
LOCAL_KEY_DIR = '/run/kai-binding-keys'
HEX_SECRET = r'[0-9a-f]{64}'
VOLUMES = {'kai_metadata_pgdata': 'kai-metadata-pgdata', 'kai_metadata_socket': 'kai-metadata-socket',
           'kai_metadata_binding_keys': 'kai-metadata-binding-keys'}
DOCKER_ENV_KEYS = ('PATH', 'HOME', 'TMPDIR', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'SYSTEMROOT')


def configuration_updates(fields):
    """Validate all inputs before generating secrets; quote keyword DSNs for dotenv."""
    get = lambda key, default='': bootstrap.value(fields.get(key, default))
    if not bootstrap.mcp_enabled(fields):
        raise ValueError('Enable MCP in root .env before local metadata setup.')
    bootstrap.optional_updates(fields)  # Validate existing MCP target and optional settings.
    flag = get('KAI_LOCAL_METADATA_ENABLED', 'false')
    if flag not in ('true', 'false'):
        raise ValueError('Local metadata flag must be true or false.')
    if get('KAI_BINDING_KEY_SOURCE'):
        raise ValueError('Host keyring is configured; local metadata cannot replace it.')
    if get('KAI_BINDING_KEY_DIR') not in ('', LOCAL_KEY_DIR):
        raise ValueError('Local metadata requires the dedicated container key directory.')
    if get('KAI_BINDING_ACTIVE_KEY_VERSION', '1') not in ('', '1'):
        raise ValueError('Local development keyring supports active version 1 only.')
    if get('KAI_BINDING_MODE', 'managed') != 'managed':
        raise ValueError('Local metadata requires managed binding mode.')
    existing = []
    admin = get('KAI_METADATA_ADMIN_PASSWORD')
    if admin and not re.fullmatch(HEX_SECRET, admin):
        raise ValueError('Local metadata admin password must be generated 64-hex.')
    if admin:
        existing.append(admin)
    missing = []
    for name, role in RUNTIME_ROLES.items():
        dsn = get(name)
        if dsn:
            match = re.fullmatch(rf'host=/run/kai-metadata dbname=kai_metadata user={role} password=({HEX_SECRET})', dsn)
            if not match:
                raise ValueError('Existing metadata DSN is external or has an unsupported local shape.')
            existing.append(match[1])
        else:
            missing.append(name)
    if len(existing) != len(set(existing)):
        raise ValueError('Local metadata passwords must be distinct.')
    if flag == 'true' and (not admin or missing):
        raise ValueError('Incomplete enabled local metadata configuration; inspect privately.')
    updates = {}
    if not admin:
        updates['KAI_METADATA_ADMIN_PASSWORD'] = secrets.token_hex(32)
    for name in missing:
        updates[name] = "'host=/run/kai-metadata dbname=kai_metadata user=" + RUNTIME_ROLES[name] + ' password=' + secrets.token_hex(32) + "'"
    for name, value in {'KAI_LOCAL_METADATA_ENABLED': 'true', 'KAI_BINDING_KEY_DIR': LOCAL_KEY_DIR}.items():
        if get(name) != value:
            updates[name] = value
    return updates


def validate_volume_metadata(volume):
    labels = volume.get('Labels') or {}
    if (volume.get('Driver') != 'local' or volume.get('Scope') != 'local' or volume.get('Options')
            or volume.get('Name') not in VOLUMES or labels.get('com.docker.compose.project') != 'kai'
            or labels.get('com.docker.compose.volume') != VOLUMES[volume['Name']]):
        raise ValueError('Local metadata volume belongs to an unknown project; inspect privately.')


def initialize_keyring(root, uid=10001, gid=10001):
    """Runs inside the isolated key-volume container; never returns key material."""
    import os
    from pathlib import Path
    import re
    import secrets
    import stat
    root = Path(root)
    created = not root.exists()
    if created:
        root.mkdir(mode=0o700)
        os.chown(root, uid, gid)
    info = root.lstat()
    # A newly created Docker volume is an empty, root-owned 0755 directory.
    if info.st_uid == 0 and not list(root.iterdir()) and not root.is_symlink():
        root.chmod(0o700)
        os.chown(root, uid, gid)
        info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Unsafe local key directory; inspect privately.')
    entries = list(root.iterdir())
    for entry in entries:
        info = entry.lstat()
        if (not re.fullmatch(r'[1-9][0-9]*\.key', entry.name) or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1 or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 32):
            raise ValueError('Unsafe local key entry; inspect privately.')
    if entries and not (root / '1.key').exists():
        raise ValueError('Local key version 1 is missing; inspect privately.')
    if not entries:
        fd = os.open(root / '1.key', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output:
            os.fchown(output.fileno(), uid, gid)
            output.write(secrets.token_bytes(32))
            output.flush()
            os.fsync(output.fileno())


OWNER_SQL = """DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kai_migrator') THEN
  CREATE ROLE kai_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kai_migrator' AND
  (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
 OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member='kai_migrator'::regrole)
 OR EXISTS(SELECT 1 FROM pg_database WHERE datname=current_database() AND datname<>'kai_metadata')
 OR EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='kai' AND nspowner<>'kai_migrator'::regrole)
 THEN RAISE EXCEPTION 'Unsafe local metadata owner or database'; END IF;
END $$;
ALTER DATABASE kai_metadata OWNER TO kai_migrator;
"""


class LocalMetadata:
    def __init__(self, root, environment=None):
        self.root = Path(root)
        source = os.environ if environment is None else environment
        self.environment = {key: source[key] for key in DOCKER_ENV_KEYS if key in source}
        self.compose = ['docker', 'compose', '--project-name', 'kai', '--project-directory', 'deploy', '--env-file', '.env']
        for overlay in ('upstream/docker-compose.yml', 'docker-compose.override.yml', 'docker-compose.mcp.yml', 'docker-compose.metadata-local.yml'):
            self.compose.extend(['-f', 'deploy/' + overlay])

    def run(self, command, input=None):
        try:
            result = subprocess.run(command, input=input, text=True, capture_output=True, cwd=self.root, env=self.environment, timeout=180)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('Local Docker command unavailable; inspect Docker privately.') from None
        if result.returncode:
            # Docker/psql diagnostics may include secrets or caller configuration.
            raise ValueError('Local metadata command failed; inspect container state privately.')
        return result.stdout.strip()

    def validate_docker(self):
        host = self.environment.get('DOCKER_HOST', '')
        if host and not host.startswith('unix:///'):
            raise ValueError('Local metadata requires a local Unix Docker endpoint.')
        data = json.loads(self.run(['docker', 'context', 'inspect']))
        if len(data) != 1 or not data[0].get('Endpoints', {}).get('docker', {}).get('Host', '').startswith('unix:///'):
            raise ValueError('Local metadata requires a local Unix Docker context.')
        if self.run(['docker', 'info', '--format', '{{.OSType}}']) != 'linux':
            raise ValueError('Local metadata requires Linux Docker containers.')
        names = set(self.run(['docker', 'volume', 'ls', '--format', '{{.Name}}']).splitlines())
        for name in VOLUMES:
            if name in names:
                entries = json.loads(self.run(['docker', 'volume', 'inspect', name]))
                if len(entries) != 1:
                    raise ValueError('Unexpected local volume inspection result.')
                validate_volume_metadata(entries[0])
        self.run(['docker', 'image', 'inspect', 'kai-clickhouse-mcp:0.7.0', '--format', '{{.Id}}'])

    def start_command(self):
        return self.compose + ['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120', 'kai-metadata']

    def psql(self, suffix):
        return self.compose + ['exec', '-T', 'kai-metadata', 'sh', '-ec',
            'export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql -X -h /var/run/postgresql -U postgres -d kai_metadata -v ON_ERROR_STOP=1 ' + suffix]

    def password_command(self, role):
        if role not in RUNTIME_ROLES.values():
            raise ValueError('Unsupported local runtime role.')
        return self.psql('-c ' + "'\\password " + role + "'")

    def provision(self, fields):
        self.run(self.start_command())
        self.run(['docker', 'volume', 'create', '--label', 'com.docker.compose.project=kai',
                  '--label', 'com.docker.compose.volume=kai-metadata-binding-keys', 'kai_metadata_binding_keys'])
        key_script = inspect.getsource(initialize_keyring) + "\ntry:\n initialize_keyring('/keys')\nexcept Exception:\n raise SystemExit('Local key initialization refused; inspect privately.')\n"
        self.run(['docker', 'run', '--rm', '--network', 'none', '--pull', 'never', '--user', '0:0',
                  '--read-only', '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--cap-add', 'DAC_OVERRIDE',
                  '--security-opt', 'no-new-privileges:true', '-i', '--mount',
                  'type=volume,source=kai_metadata_binding_keys,target=/keys', '--entrypoint', 'python',
                  'kai-clickhouse-mcp:0.7.0', '-'], input=key_script)
        self.run(['docker', 'run', '--rm', '--network', 'none', '--pull', 'never', '--user', '10001:10001',
                  '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                  '--mount', 'type=volume,source=kai_metadata_binding_keys,target=/run/kai-binding-keys,readonly',
                  '--entrypoint', 'python', 'kai-clickhouse-mcp:0.7.0', '-c',
                  "from credential_crypto import load_keyring; keys=load_keyring('/run/kai-binding-keys'); assert 1 in keys and len(keys[1]) == 32"])
        self.run(self.psql(''), input=OWNER_SQL)
        for filename in ('migrate_identity.sql', 'migrate_approvals.sql', 'migrate_enrollment.sql', 'migrate_admin_warehouse.sql', 'migrate_revocation_fence.sql'):
            self.run(self.psql(''), input='SET ROLE kai_migrator;\n\\i /migrations/' + filename + '\n')
        for filename in ('create_binding_roles.sql', 'create_enrollment_role.sql'):
            self.run(self.psql(''), input='\\i /migrations/' + filename + '\n')
        for filename in ('grant_mcp_bindings.sql', 'grant_enrollment_access.sql'):
            self.run(self.psql(''), input='SET ROLE kai_migrator;\n\\i /migrations/' + filename + '\n')
        self.run(self.psql(''), input='\\i /migrations/provision_warehouse_runtime.sql\n')
        for name, role in RUNTIME_ROLES.items():
            # Reassign the same configured password on retry; psql encrypts SCRAM client-side.
            password = bootstrap.value(fields[name]).rsplit('=', 1)[1]
            self.run(self.password_command(role), input=password + '\n' + password + '\n')
            self.run(self.psql(''), input='ALTER ROLE ' + role + ' LOGIN;\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', action='store_true', help='Validate configuration and local Docker without changes.')
    modes.add_argument('--apply', action='store_true', help='Initialize persistent local metadata; does not restart API/MCP.')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    helper = LocalMetadata(root)
    # --check must not create bootstrap's lock file or change .env permissions.
    def read():
        path = root / '.env'
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('Root .env must be an owned, unlinked regular file.')
        text = path.read_text()
        fields = bootstrap.settings(text)
        updates = configuration_updates(fields)
        return path, text, fields, updates
    try:
        read()
        helper.validate_docker()
        if args.check:
            print('Local metadata prerequisites valid; --apply initializes metadata and preserves existing secrets.')
            return 0
        with bootstrap.environment_lock(root):
            path, text, fields, updates = read()
            if updates:
                bootstrap.write_environment(path, bootstrap.patch_settings(text, updates))
            helper.provision(bootstrap.settings(path.read_text()))
        print('Local metadata initialized. Apply the MCP overlay separately to activate socket/key mounts; no users enrolled.')
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as error:
        # ValueError texts in this module are fixed strings (no values); other errors print their type only.
        reason = str(error) if type(error) is ValueError else type(error).__name__
        print(f'Local metadata setup refused or failed: {reason} No secret values are printed.', file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
