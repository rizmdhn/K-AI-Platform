import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def render(tls=False, bindings=False):
    with tempfile.TemporaryDirectory() as directory:
        fixture = Path(directory) / "fixture.env"
        fixture.write_text(
            "PORT=3080\nUID=1000\nGID=1000\nPOSTGRES_DB=test\nPOSTGRES_USER=test\n"
            "POSTGRES_PASSWORD=fixture\nCLICKHOUSE_HOST=db.example.com\nCLICKHOUSE_PORT=8443\n"
            "CLICKHOUSE_MCP_AUTH_TOKEN=fixture-token\nKAI_PLATFORM_INTERNAL_TOKEN=internal-fixture-token\nCLICKHOUSE_USER=kai_unbound\nKAI_ENROLLMENT_DSN=postgresql://fixture:fixture@metadata.invalid/kai\nKAI_METRICS_TOKEN=cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc\nJWT_SECRET=fixture-app-token\nOLLAMA_API_KEY=fixture-model-key\nCLICKHOUSE_PASSWORD=fixture-base-password\nKAI_BINDING_KEY_SOURCE=/fixture/private-keys\nKAI_BINDING_KEY_DIR=/legacy/keyring\n"
        )
        files = [
            "deploy/upstream/docker-compose.yml",
            "deploy/docker-compose.override.yml",
            "deploy/docker-compose.mcp.yml",
        ]
        if tls:
            files.append("deploy/docker-compose.tls.yml")
        if bindings:
            files.append("deploy/docker-compose.bindings.yml")
        command = [
            "docker",
            "compose",
            "--project-directory",
            "deploy",
            "--env-file",
            str(fixture),
        ]
        for file in files:
            command.extend(["-f", file])
        result = subprocess.run(
            command + ["--profile", "*", "config", "--format", "json"],
            cwd=ROOT,
            env={
                **{
                    key: os.environ[key]
                    for key in (
                        "PATH",
                        "HOME",
                        "TMPDIR",
                        "DOCKER_HOST",
                        "DOCKER_CONTEXT",
                        "DOCKER_CONFIG",
                        "SYSTEMROOT",
                    )
                    if key in os.environ
                },
                "PORT": "3080",
                "KAI_DOMAIN": "chat.example.com",
            },
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)


class MCPComposeTests(unittest.TestCase):
    def test_make_stops_when_overlay_selection_refuses_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / "scripts").mkdir()
            shutil.copyfile(ROOT / "Makefile", stage / "Makefile")
            shutil.copyfile(
                ROOT / "scripts/bootstrap.py", stage / "scripts/bootstrap.py"
            )
            (stage / ".env").write_text(
                "KAI_MCP_ENABLED=true\nKAI_BINDING_KEY_SOURCE=relative/keys\n"
            )
            result = subprocess.run(
                ["make", "-n", "down"], cwd=stage, capture_output=True, text=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("docker compose", result.stdout)

    def test_optional_keyring_is_read_only_mcp_only_and_never_created_implicitly(self):
        config = render(bindings=True)
        mounts = config["services"]["clickhouse-mcp"]["volumes"]
        key = next(
            mount for mount in mounts if mount["target"] == "/run/kai-binding-keys"
        )
        self.assertEqual(key["source"], "/fixture/private-keys")
        self.assertTrue(key["read_only"])
        self.assertFalse(key["bind"]["create_host_path"])
        self.assertEqual(
            config["services"]["clickhouse-mcp"]["environment"]["KAI_BINDING_KEY_DIR"],
            key["target"],
        )
        for name in ("api", "admin-panel"):
            self.assertFalse(
                any(
                    mount.get("source") == "/fixture/private-keys"
                    for mount in config["services"][name].get("volumes", [])
                )
            )

    def test_document_retrieval_stays_disabled_pending_acl_and_embedding_acceptance(
        self,
    ):
        config = render()
        self.assertEqual(config["services"]["api"]["environment"]["RAG_API_URL"], "")
        self.assertEqual(config["services"]["rag_api"]["profiles"], ["documents"])
        self.assertEqual(config["services"]["vectordb"]["profiles"], ["documents"])
        self.assertNotIn("rag_api", config["services"]["api"].get("depends_on", {}))

    def test_private_service_has_only_api_network_and_egress(self):
        config = render()
        mcp = config["services"]["clickhouse-mcp"]
        self.assertFalse(mcp.get("ports"))
        self.assertEqual(set(mcp["networks"]), {"tools_private", "tool_egress", "embed_private"})
        self.assertTrue(config["networks"]["embed_private"]["internal"])
        self.assertEqual(
            {name for name, service in config["services"].items() if "embed_private" in service.get("networks", {})},
            {"clickhouse-mcp", "embeddings"},
        )
        self.assertTrue(config["networks"]["tools_private"]["internal"])
        # vertex-proxy (internet egress) shares an internal network with api only, never with the MCP.
        self.assertEqual(
            {name for name, service in config["services"].items()
             if "model_private" in service.get("networks", {})},
            {"api", "vertex-proxy"},
        )
        self.assertTrue(config["networks"]["model_private"]["internal"])
        self.assertNotIn("ports", config["services"]["vertex-proxy"])
        self.assertEqual(set(config["services"]["vertex-proxy"]["networks"]), {"model_private", "model_egress"})
        self.assertEqual(
            set(config["services"]["api"]["networks"]), {"default", "tools_private", "documents_private", "model_private"}
        )
        self.assertEqual(
            set(config["services"]["admin-panel"]["networks"]), {"default"}
        )
        self.assertTrue(
            config["services"]["admin-panel"]["build"]["context"].endswith(
                "/services/admin-panel"
            )
        )
        self.assertNotIn("tools_private", config["services"]["mongodb"]["networks"])
        self.assertEqual(mcp["environment"]["CLICKHOUSE_SECURE"], "true")
        self.assertEqual(mcp["environment"]["CLICKHOUSE_VERIFY"], "true")
        self.assertEqual(mcp["environment"]["CLICKHOUSE_ALLOW_WRITE_ACCESS"], "false")
        self.assertEqual(
            mcp["environment"]["MCP_MIDDLEWARE_MODULE"], "per_user_middleware"
        )
        self.assertEqual(mcp["environment"]["KAI_BINDING_MODE"], "managed")
        self.assertEqual(
            config["services"]["api"]["environment"]["CLICKHOUSE_MCP_AUTH_TOKEN"],
            "fixture-token",
        )
        for service in (config["services"]["api"], mcp):
            self.assertEqual(
                service["environment"]["KAI_PLATFORM_INTERNAL_TOKEN"], "internal-fixture-token"
            )
        self.assertEqual(
            {name for name, service in config["services"].items()
             if "tools_private" in service.get("networks", {})},
            {"api", "clickhouse-mcp"},
        )
        self.assertTrue(config["networks"]["tools_private"]["internal"])
        self.assertNotIn("ports", mcp)
        self.assertTrue(mcp["read_only"])
        self.assertEqual(
            mcp["logging"],
            {"driver": "local", "options": {"max-size": "10m", "max-file": "3"}},
        )
        self.assertNotIn("KAI_METRICS_TOKEN", config["services"]["api"]["environment"])
        self.assertNotIn(
            "CLICKHOUSE_PASSWORD", config["services"]["api"]["environment"]
        )
        self.assertNotIn("OLLAMA_API_KEY", mcp["environment"])
        self.assertNotIn("JWT_SECRET", mcp["environment"])
        self.assertNotIn(
            "KAI_METRICS_TOKEN", config["services"]["rag_api"]["environment"]
        )
        self.assertEqual(mcp["environment"]["KAI_METRICS_TOKEN"], "c" * 64)

    def test_exported_host_secrets_cannot_override_test_fixture(self):
        from unittest.mock import patch

        with patch.dict(
            os.environ,
            {
                "CLICKHOUSE_MCP_AUTH_TOKEN": "synthetic-host-token",
                "KAI_METRICS_TOKEN": "d" * 64,
                "CLICKHOUSE_HOST": "other.example.invalid",
            },
        ):
            config = render()
        self.assertEqual(
            config["services"]["api"]["environment"]["CLICKHOUSE_MCP_AUTH_TOKEN"],
            "fixture-token",
        )
        self.assertEqual(
            config["services"]["clickhouse-mcp"]["environment"]["KAI_METRICS_TOKEN"],
            "c" * 64,
        )
        self.assertEqual(
            config["services"]["clickhouse-mcp"]["environment"]["CLICKHOUSE_HOST"],
            "db.example.com",
        )

    def test_tls_keeps_mcp_private_and_api_connected(self):
        config = render(tls=True)
        ports = [
            (name, port)
            for name, service in config["services"].items()
            for port in service.get("ports", [])
        ]
        self.assertEqual(len(ports), 1)
        self.assertEqual(ports[0][0], "reverse-proxy")
        self.assertEqual(ports[0][1]["target"], 443)
        self.assertEqual(
            set(config["services"]["api"]["networks"]),
            {"default", "tools_private", "documents_private", "model_private", "web"},
        )
        self.assertNotIn("web", config["services"]["clickhouse-mcp"]["networks"])
        self.assertEqual(
            config["services"]["reverse-proxy"]["logging"],
            {"driver": "local", "options": {"max-size": "10m", "max-file": "3"}},
        )

    def test_make_preserves_configured_mcp_overlay_for_lifecycle_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            shutil.copyfile(ROOT / "Makefile", stage / "Makefile")
            scripts = stage / "scripts"
            scripts.mkdir()
            shutil.copyfile(ROOT / "scripts/bootstrap.py", scripts / "bootstrap.py")
            absent = subprocess.run(
                ["make", "-n", "up"],
                cwd=stage,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertNotIn("-f deploy/docker-compose.mcp.yml", absent.stdout)
            (stage / ".env").write_text("KAI_MCP_ENABLED=true\n")
            for target in ("up", "down", "logs", "ps", "check"):
                with self.subTest(target=target):
                    present = subprocess.run(
                        ["make", "-n", target],
                        cwd=stage,
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    self.assertIn("-f deploy/docker-compose.mcp.yml", present.stdout)


if __name__ == "__main__":
    unittest.main()
