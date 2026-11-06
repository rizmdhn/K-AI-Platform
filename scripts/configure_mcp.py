"""Configure optional read-only MCP in the unified root .env without printing secrets."""
import argparse
import importlib.util
from pathlib import Path
import secrets

spec = importlib.util.spec_from_file_location('kai_bootstrap', Path(__file__).with_name('bootstrap.py'))
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def configure(root: Path, host: str, port: int) -> bool:
    bootstrap.validate_mcp_target(host, port)
    def transform(fields):
        value = bootstrap.value
        updates = {}
        defaults = {'CLICKHOUSE_HOST': host, 'CLICKHOUSE_PORT': str(port), 'CLICKHOUSE_SECURE': 'true',
                    'CLICKHOUSE_VERIFY': 'true', 'CLICKHOUSE_USER': 'kai_unbound',
                    'CLICKHOUSE_PASSWORD': secrets.token_hex(32), 'CLICKHOUSE_MCP_AUTH_TOKEN': secrets.token_hex(32),
                    'KAI_PLATFORM_INTERNAL_TOKEN': secrets.token_hex(32)}
        # Preserve an already configured target and all existing credentials.
        if not value(fields.get('CLICKHOUSE_HOST', '')):
            updates['CLICKHOUSE_HOST'] = host
            updates['CLICKHOUSE_PORT'] = str(port)
        for key, raw in defaults.items():
            if not value(fields.get(key, '')):
                updates.setdefault(key, raw)
        if not bootstrap.mcp_enabled(fields):
            updates['KAI_MCP_ENABLED'] = 'true'
        return updates
    return bootstrap.update_configuration(root, transform)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=8443)
    args = parser.parse_args()
    try:
        changed = configure(Path(__file__).resolve().parents[1], args.host, args.port)
        print('MCP settings saved in root .env.' if changed else 'Existing MCP settings preserved.')
    except (ValueError, OSError):
        parser.exit(1, 'MCP configuration refused; inspect root .env privately. No values printed.\n')
