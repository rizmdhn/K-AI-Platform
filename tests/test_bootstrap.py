import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def test_https_overlay_whenever_a_hostname_or_ipv4_is_set(self):
        select = self.bootstrap.tls_overlay
        self.assertEqual(select({}), "")
        for domain in ("kai.example.co.id", "13.212.45.67"):
            self.assertEqual(select({"KAI_DOMAIN": domain}), "-f deploy/docker-compose.tls.yml")
        for bad in ("https://kai.example.co.id", "kai.example.co.id/x", "13.212.45.67:443", "a b"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                select({"KAI_DOMAIN": bad})

    def test_vertex_overlay_only_with_an_absolute_service_account_path(self):
        select = self.bootstrap.vertex_overlay
        self.assertEqual(select({}), "")
        self.assertEqual(select({"KAI_VERTEX_KEY_FILE": "/etc/kai/vertex.json"}), " -f deploy/docker-compose.vertex.yml")
        for bad in ("vertex.json", "/etc/kai/my key.json", "/etc/kai/a:b.json"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                select({"KAI_VERTEX_KEY_FILE": bad})

    def test_binding_mount_selection_requires_mcp_and_absolute_explicit_source(self):
        self.assertTrue(callable(getattr(self.bootstrap, "mcp_overlays", None)))
        select = self.bootstrap.mcp_overlays
        self.assertEqual(
            select({"KAI_MCP_ENABLED": "false", "KAI_BINDING_KEY_SOURCE": "/srv/keys"}),
            "",
        )
        self.assertEqual(
            select({"KAI_MCP_ENABLED": "true"}), "-f deploy/docker-compose.mcp.yml"
        )
        self.assertEqual(
            select(
                {
                    "KAI_MCP_ENABLED": "true",
                    "KAI_BINDING_KEY_SOURCE": "'/srv/private keys'",
                }
            ),
            "-f deploy/docker-compose.mcp.yml -f deploy/docker-compose.bindings.yml",
        )
        with self.assertRaises(ValueError):
            select(
                {"KAI_MCP_ENABLED": "true", "KAI_BINDING_KEY_SOURCE": "relative/keys"}
            )

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "bootstrap", ROOT / "scripts" / "bootstrap.py"
        )
        self.bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bootstrap)

    def test_creates_private_environment_with_unique_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            self.assertTrue(self.bootstrap.initialize(runtime))
            environment = runtime / ".env"
            values = dict(
                line.split("=", 1) for line in environment.read_text().splitlines()
            )
            self.assertEqual(stat.S_IMODE(environment.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o700)
            secrets = [
                values[name]
                for name in (
                    "CREDS_KEY",
                    "JWT_SECRET",
                    "JWT_REFRESH_SECRET",
                    "MEILI_MASTER_KEY",
                    "POSTGRES_PASSWORD",
                )
            ]
            self.assertEqual(len(set(secrets)), 5)
            self.assertTrue(all(len(value) == 64 for value in secrets))
            self.assertEqual(len(values["CREDS_IV"]), 32)
            self.assertEqual(values["OLLAMA_API_KEY"], "")
            self.assertEqual(len(values["ADMIN_PANEL_SESSION_SECRET"]), 64)
            self.assertEqual(values["ADMIN_PANEL_URL"], "http://localhost:3081")

    def test_rerun_preserves_credentials_and_user_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            self.bootstrap.initialize(runtime)
            environment = runtime / ".env"
            original = environment.read_bytes() + b"CUSTOM_SETTING=keep-me\n"
            environment.write_bytes(original)
            self.assertFalse(self.bootstrap.initialize(runtime))
            self.assertEqual(environment.read_bytes(), original)

    def test_migrates_legacy_environment_without_rotating_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "deploy/runtime"
            self.bootstrap.initialize(legacy)
            original = (legacy / ".env").read_bytes()
            self.assertTrue(self.bootstrap.initialize(root))
            self.assertEqual((root / ".env").read_bytes(), original)
            self.assertFalse((legacy / ".env").exists())
            self.assertFalse(self.bootstrap.initialize(root))

    def test_conflicting_root_and_legacy_files_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "deploy/runtime"
            self.bootstrap.initialize(legacy)
            (root / ".env").write_text("JWT_SECRET=root-value\n")
            before = (legacy / ".env").read_bytes()
            with self.assertRaises(ValueError):
                self.bootstrap.initialize(root)
            self.assertEqual((root / ".env").read_text(), "JWT_SECRET=root-value\n")
            self.assertEqual((legacy / ".env").read_bytes(), before)

    def test_merges_mcp_and_metrics_into_root_without_rotating_existing_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.bootstrap.initialize(root)
            before = (root / ".env").read_text()
            legacy = root / "deploy/runtime"
            legacy.mkdir(parents=True, mode=0o700)
            mcp = legacy / "mcp.env"
            token = "a" * 64
            mcp.write_text(
                "CLICKHOUSE_HOST=fixture.example.invalid\nCLICKHOUSE_PORT=8443\nCLICKHOUSE_SECURE=true\nCLICKHOUSE_VERIFY=true\nCLICKHOUSE_USER=kai_unbound\nCLICKHOUSE_PASSWORD="
                + "b" * 64
                + "\nCLICKHOUSE_MCP_AUTH_TOKEN="
                + token
                + "\n"
            )
            mcp.chmod(0o600)
            metrics = legacy / "metrics.env"
            metrics.write_text("KAI_METRICS_TOKEN=" + "c" * 64 + "\n")
            metrics.chmod(0o600)
            self.assertTrue(self.bootstrap.initialize(root))
            text = (root / ".env").read_text()
            self.assertIn("CLICKHOUSE_MCP_AUTH_TOKEN=" + token + "\n", text)
            self.assertIn("KAI_METRICS_TOKEN=" + "c" * 64 + "\n", text)
            self.assertIn("KAI_MCP_ENABLED=true\n", text)
            # Upgrading an MCP install adds a distinct platform-internal token.
            internal = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)["KAI_PLATFORM_INTERNAL_TOKEN"]
            self.assertEqual(len(internal), 64)
            self.assertNotIn(internal, (token, "b" * 64, "c" * 64))
            for line in before.splitlines():
                if line.startswith(("JWT_SECRET=", "CREDS_KEY=", "OLLAMA_API_KEY=")):
                    self.assertIn(line + "\n", text)
            self.assertFalse(mcp.exists())
            self.assertFalse(metrics.exists())
            self.assertFalse(self.bootstrap.initialize(root))

    def test_conflicting_auxiliary_values_preserve_both_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.bootstrap.initialize(root)
            path = root / ".env"
            path.write_text(
                path.read_text().replace(
                    "CLICKHOUSE_HOST=\n", "CLICKHOUSE_HOST=root.example.invalid\n"
                )
            )
            before = path.read_bytes()
            legacy = root / "deploy/runtime"
            legacy.mkdir(parents=True, mode=0o700)
            source = legacy / "mcp.env"
            source.write_text("CLICKHOUSE_HOST=other.example.invalid\n")
            source.chmod(0o600)
            with self.assertRaises(ValueError):
                self.bootstrap.initialize(root)
            self.assertEqual(path.read_bytes(), before)
            self.assertTrue(source.exists())

    def test_invalid_legacy_metrics_remains_recoverable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.bootstrap.initialize(root)
            before = (root / ".env").read_bytes()
            legacy = root / "deploy/runtime"
            legacy.mkdir(parents=True, mode=0o700)
            source = legacy / "metrics.env"
            source.write_text("KAI_METRICS_TOKEN=invalid\n")
            source.chmod(0o600)
            with self.assertRaises(ValueError):
                self.bootstrap.initialize(root)
            self.assertEqual((root / ".env").read_bytes(), before)
            self.assertTrue(source.exists())

    def test_indented_existing_secret_is_never_rotated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.bootstrap.initialize(root)
            path = root / ".env"
            text = path.read_text()
            line = next(
                line for line in text.splitlines() if line.startswith("CREDS_KEY=")
            )
            path.write_text(text.replace(line, "  " + line))
            self.assertFalse(self.bootstrap.initialize(root))
            self.assertEqual(path.read_text().count("CREDS_KEY="), 1)
            self.assertIn("  " + line + "\n", path.read_text())

    def test_migrated_legacy_template_completes_secrets_in_one_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "deploy/runtime"
            legacy.mkdir(parents=True, mode=0o700)
            (legacy / ".env").write_text("CREDS_KEY=\nJWT_SECRET=\n")
            (legacy / ".env").chmod(0o600)
            self.bootstrap.initialize(root)
            fields = dict(
                line.split("=", 1)
                for line in (root / ".env").read_text().splitlines()
                if line and not line.startswith("#")
            )
            self.assertEqual(len(fields["CREDS_KEY"]), 64)
            self.assertEqual(len(fields["JWT_SECRET"]), 64)

    def test_unparsed_legacy_line_is_not_discarded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.bootstrap.initialize(root)
            original = (root / ".env").read_bytes()
            legacy = root / "deploy/runtime"
            legacy.mkdir(parents=True, mode=0o700)
            source = legacy / "metrics.env"
            source.write_text(
                "KAI_METRICS_TOKEN=" + "c" * 64 + "\nUNSUPPORTED: preserve-me\n"
            )
            source.chmod(0o600)
            with self.assertRaises(ValueError):
                self.bootstrap.initialize(root)
            self.assertEqual((root / ".env").read_bytes(), original)
            self.assertTrue(source.exists())

    def test_matching_legacy_copy_secures_root_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "deploy/runtime"
            self.bootstrap.initialize(legacy)
            (root / ".env").write_bytes((legacy / ".env").read_bytes())
            (root / ".env").chmod(0o644)
            self.bootstrap.initialize(root)
            self.assertEqual((root / ".env").stat().st_mode & 0o777, 0o600)

    def test_concurrent_template_setup_does_not_replace_published_secrets(self):
        import threading
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text((ROOT / ".env.example").read_text())
            first_writing = threading.Event()
            release_first = threading.Event()
            second_done = threading.Event()
            results, failures = [], []
            original_replace = self.bootstrap.os.replace

            def replace(source, destination):
                if threading.current_thread().name == "first-setup":
                    first_writing.set()
                    if not release_first.wait(5):
                        raise TimeoutError("First setup was not released")
                return original_replace(source, destination)

            def run(second=False):
                try:
                    results.append(self.bootstrap.initialize(root))
                except Exception as error:
                    failures.append(error)
                finally:
                    if second:
                        second_done.set()

            first = threading.Thread(target=run, name="first-setup")
            second = threading.Thread(target=run, kwargs={"second": True})
            with patch.object(self.bootstrap.os, "replace", side_effect=replace):
                try:
                    first.start()
                    self.assertTrue(first_writing.wait(3))
                    second.start()
                    self.assertFalse(
                        second_done.wait(0.3), "Second setup bypassed the first writer"
                    )
                finally:
                    release_first.set()
                    first.join(5)
                    if second.ident is not None:
                        second.join(5)
            self.assertFalse(failures)
            self.assertEqual(sorted(results), [False, True])

    def test_managed_root_link_preserves_external_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "deploy/runtime"
            self.bootstrap.initialize(runtime)
            original = (runtime / ".env").read_bytes()
            (root / ".env").symlink_to(runtime / ".env")
            self.assertFalse(self.bootstrap.initialize(root))
            self.assertTrue((root / ".env").is_symlink())
            self.assertEqual((runtime / ".env").read_bytes(), original)

    def test_copied_root_example_gets_unique_secrets_without_losing_model_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template = (ROOT / ".env.example").read_text()
            (root / ".env").write_text(
                template.replace(
                    "OLLAMA_API_KEY=", "OLLAMA_API_KEY=fixture-provider-key"
                )
            )
            self.assertTrue(self.bootstrap.initialize(root))
            text = (root / ".env").read_text()
            values = dict(
                line.split("=", 1)
                for line in text.splitlines()
                if line and not line.startswith("#")
            )
            self.assertEqual(values["OLLAMA_API_KEY"], "fixture-provider-key")
            keys = [
                values[k]
                for k in [
                    "CREDS_KEY",
                    "JWT_SECRET",
                    "JWT_REFRESH_SECRET",
                    "MEILI_MASTER_KEY",
                    "ADMIN_PANEL_SESSION_SECRET",
                ]
            ]
            self.assertEqual(len(set(keys)), len(keys))
            self.assertTrue(all(len(key) == 64 for key in keys))
            self.assertEqual((root / ".env").stat().st_mode & 0o777, 0o600)
            self.assertFalse(self.bootstrap.initialize(root))
            self.assertEqual((root / ".env").read_text(), text)


if __name__ == "__main__":
    unittest.main()

class LocalMetadataOverlayTests(unittest.TestCase):
    def test_local_metadata_overlay_requires_mcp_and_excludes_host_keyring(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('local_metadata_bootstrap',Path(__file__).resolve().parents[1]/'scripts/bootstrap.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        select=module.mcp_overlays
        self.assertEqual(select({'KAI_MCP_ENABLED':'true','KAI_LOCAL_METADATA_ENABLED':'true'}),'-f deploy/docker-compose.mcp.yml -f deploy/docker-compose.metadata-local.yml')
        for fields in [
            {'KAI_MCP_ENABLED':'false','KAI_LOCAL_METADATA_ENABLED':'true'},
            {'KAI_MCP_ENABLED':'true','KAI_LOCAL_METADATA_ENABLED':'true','KAI_BINDING_KEY_SOURCE':'/srv/keys'},
            {'KAI_MCP_ENABLED':'true','KAI_LOCAL_METADATA_ENABLED':'maybe'},
        ]:
            with self.assertRaises(ValueError):select(fields)
