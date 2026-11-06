"""Create the optional private metrics secret in root .env, without printing or rotating it."""
import importlib.util
from pathlib import Path
import secrets
import sys

spec = importlib.util.spec_from_file_location('kai_bootstrap', Path(__file__).with_name('bootstrap.py'))
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def configure(root: Path) -> bool:
    # Refuse indirect targets in this operator helper; edit managed runtime through its owner.
    path = root / '.env'
    if path.is_symlink() or (path.exists() and path.stat().st_mode & 0o077):
        raise ValueError('Root environment must be a private regular file.')
    def transform(fields):
        if bootstrap.value(fields.get('KAI_METRICS_TOKEN', '')):
            return {}
        token = secrets.token_hex(32)
        taken = {bootstrap.value(fields.get(key, '')) for key in ('CLICKHOUSE_MCP_AUTH_TOKEN', 'KAI_PLATFORM_INTERNAL_TOKEN')}
        while token in taken:
            token = secrets.token_hex(32)
        return {'KAI_METRICS_TOKEN': token}
    return bootstrap.update_configuration(root, transform)


if __name__ == '__main__':
    try:
        changed = configure(Path(__file__).resolve().parents[1])
        print('Metrics settings saved in root .env.' if changed else 'Existing metrics settings preserved.')
    except (ValueError, OSError):
        sys.exit('Metrics configuration refused; inspect root .env privately. No values printed.')
