import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EnvironmentHygieneTests(unittest.TestCase):
    def template_names(self):
        names = []
        for number, line in enumerate(
            (ROOT / ".env.example").read_text().splitlines(), 1
        ):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            self.assertTrue(
                re.fullmatch(r"[A-Z][A-Z0-9_]*=.*", line) is not None,
                f".env.example:{number} must be a setting",
            )
            names.append(line.split("=", 1)[0])
        self.assertEqual(
            len(names), len(set(names)), "Template contains duplicate names"
        )
        return set(names)

    def test_reference_has_only_public_defaults_and_covers_generated_bootstrap_settings(
        self,
    ):
        spec = importlib.util.spec_from_file_location(
            "bootstrap", ROOT / "scripts/bootstrap.py"
        )
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            bootstrap.initialize(runtime)
            generated_names = {
                line.split("=", 1)[0]
                for line in (runtime / ".env").read_text().splitlines()
            }
        self.assertFalse(
            generated_names - self.template_names(),
            "Generated setting names are absent from the template",
        )
        values = dict(
            line.split("=", 1)
            for line in (ROOT / ".env.example").read_text().splitlines()
            if line and not line.startswith("#")
        )
        for key in [
            "CREDS_KEY",
            "CREDS_IV",
            "JWT_SECRET",
            "JWT_REFRESH_SECRET",
            "MEILI_MASTER_KEY",
            "POSTGRES_PASSWORD",
            "ADMIN_PANEL_SESSION_SECRET",
            "OLLAMA_API_KEY",
        ]:
            self.assertEqual(values[key], "", key)
        self.assertEqual(values["CLICKHOUSE_PASSWORD"], "")
        self.assertEqual(values["KAI_METRICS_TOKEN"], "")

    def test_root_example_lists_tls_without_collecting_mcp_secrets(self):
        self.assertFalse(
            {"KAI_DOMAIN", "TRUST_PROXY", "SESSION_COOKIE_SECURE"}
            - self.template_names()
        )
        values = dict(
            line.split("=", 1)
            for line in (ROOT / ".env.example").read_text().splitlines()
            if line and not line.startswith("#")
        )
        self.assertEqual(values["CLICKHOUSE_MCP_AUTH_TOKEN"], "")
        self.assertEqual(values["KAI_PLATFORM_INTERNAL_TOKEN"], "")
        self.assertEqual(values["KAI_MCP_ENABLED"], "false")

    def ignored(self, paths):
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--stdin"],
            cwd=ROOT,
            input="\n".join(paths) + "\n",
            capture_output=True,
            text=True,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return set(result.stdout.splitlines())

    def test_git_excludes_private_runtime_material(self):
        paths = {
            ".env",
            ".env.production",
            ".env.setup.lock",
            ".kai-env-interrupted",
            "deploy/runtime/.env",
            "runtime/mcp.env",
            "services/clickhouse-mcp/mcp.env",
            "services/metadata-worker/worker.env",
            "secrets/ch_credential_master_key",
            "services/clickhouse-mcp/secrets/token",
            "deploy/id_ed25519",
            "deploy/id_rsa",
            "deploy/tls/private.key",
            "deploy/tls/private.pem",
            ".planning/STATE.md",
            "docs/PRD.md",
            "Docs/PRD.md",
            "deploy/ansible/inventories/prod/hosts.yml",
        }
        self.assertEqual(self.ignored(paths), paths)

    def test_git_keeps_templates_source_and_fixtures_available(self):
        paths = {
            ".env.example",
            "services/clickhouse-mcp/.env.example",
            "services/clickhouse-mcp/env.example",
            "services/clickhouse-mcp/per_user_middleware.py",
            "services/clickhouse-mcp/tests/e2e_clickhouse_init.sql",
            "deploy/upstream/lock.json",
            "deploy/docker-compose.tls.yml",
            "deploy/ansible/inventories/prod/hosts.example.yml",
            "deploy/id_ed25519.pub",
        }
        self.assertEqual(self.ignored(paths), set())


if __name__ == "__main__":
    unittest.main()
