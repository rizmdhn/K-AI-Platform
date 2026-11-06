import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "configure_mcp", ROOT / "scripts/configure_mcp.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class MCPSetupTests(unittest.TestCase):
    def test_creates_private_tls_config_without_personal_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            self.assertTrue(setup.configure(runtime, "db.example.com", 8443))
            config = runtime / ".env"
            values = dict(
                line.split("=", 1) for line in config.read_text().splitlines()
            )
            self.assertEqual(values["CLICKHOUSE_HOST"], "db.example.com")
            self.assertEqual(values["CLICKHOUSE_PORT"], "8443")
            self.assertEqual(values["CLICKHOUSE_SECURE"], "true")
            self.assertEqual(values["CLICKHOUSE_VERIFY"], "true")
            self.assertEqual(values["CLICKHOUSE_USER"], "kai_unbound")
            self.assertEqual(len(values["CLICKHOUSE_PASSWORD"]), 64)
            self.assertEqual(len(values["CLICKHOUSE_MCP_AUTH_TOKEN"]), 64)
            self.assertEqual(len(values["KAI_PLATFORM_INTERNAL_TOKEN"]), 64)
            secrets = {values[key] for key in (
                "CLICKHOUSE_PASSWORD", "CLICKHOUSE_MCP_AUTH_TOKEN", "KAI_PLATFORM_INTERNAL_TOKEN")}
            self.assertEqual(len(secrets), 3)
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)

    def test_rerun_preserves_transport_key_and_target(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            setup.configure(runtime, "db.example.com", 8443)
            original = (runtime / ".env").read_bytes()
            self.assertFalse(setup.configure(runtime, "other.example.com", 9440))
            self.assertEqual((runtime / ".env").read_bytes(), original)

    def test_invalid_target_leaves_no_file(self):
        for host, port in (
            ("https://db.example.com", 8443),
            ("db.example.com,", 8443),
            ("db.example.com\nTOKEN=bad", 8443),
            ("", 8443),
            ("db.example.com", 0),
            ("db.example.com", 65536),
        ):
            with self.subTest(
                host=host, port=port
            ), tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory) / "runtime"
                with self.assertRaises(ValueError):
                    setup.configure(runtime, host, port)
                self.assertFalse((runtime / ".env").exists())


if __name__ == "__main__":
    unittest.main()
