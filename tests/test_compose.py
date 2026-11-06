"""Check the effective Compose deployment, including upstream/override merge behavior."""

import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
    "docker",
    "compose",
    "--project-name",
    "kai",
    "--project-directory",
    "deploy",
    "--env-file",
    ".env",
    "-f",
    "deploy/upstream/docker-compose.yml",
    "-f",
    "deploy/docker-compose.override.yml",
]


def render(*arguments, **env):
    result = subprocess.run(
        COMPOSE + list(arguments) + ["config", "--format", "json"],
        cwd=ROOT,
        env=dict(os.environ, PORT="3080", KAI_RAG_API_URL="", **env),  # default stack: RAG off even if a local .env opts in
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


class ComposeTests(unittest.TestCase):
    def test_mongodb_network_holds_no_third_party_document_service(self):
        config = render("--profile", "*")
        on = lambda net: {n for n, svc in config["services"].items() if net in svc.get("networks", {"default": None})}
        self.assertEqual(on("default"), {"api", "mongodb", "meilisearch", "admin-panel"})
        self.assertTrue(config["networks"]["documents_private"]["internal"])
        self.assertEqual(on("documents_private"), {"api", "rag_api", "vectordb", "embeddings", "rustfs", "rustfs-init"})
        self.assertEqual(on("embed_egress"), {"embeddings"})
        api = config["services"]["api"]["environment"]
        self.assertNotEqual(api["AWS_ACCESS_KEY_ID"], config["services"]["rustfs"]["environment"]["RUSTFS_ACCESS_KEY"],
                            "api must use the bucket-scoped RustFS user, not root")

    def test_inter_service_addresses_default_to_service_names_and_can_point_elsewhere(self):
        keys = ("KAI_MONGO_URI", "KAI_MEILI_HOST", "KAI_S3_ENDPOINT", "KAI_VECTORDB_HOST", "KAI_EMBEDDINGS_BASE_URL")
        local = render("--profile", "*", **{k: "" for k in keys})["services"]
        self.assertEqual(local["api"]["environment"]["MONGO_URI"], "mongodb://mongodb:27017/LibreChat")
        self.assertEqual(local["api"]["environment"]["AWS_ENDPOINT_URL"], "http://rustfs:9000")
        self.assertEqual(local["rag_api"]["environment"]["DB_HOST"], "vectordb")
        split = render("--profile", "*", KAI_S3_ENDPOINT="https://files.internal:9000",
                       KAI_EMBEDDINGS_BASE_URL="https://ai.internal/v1", KAI_VECTORDB_HOST="10.0.0.5")["services"]
        self.assertEqual(split["api"]["environment"]["AWS_ENDPOINT_URL"], "https://files.internal:9000")
        self.assertEqual(split["rag_api"]["environment"]["RAG_OPENAI_BASEURL"], "https://ai.internal/v1")
        self.assertEqual(split["rag_api"]["environment"]["DB_HOST"], "10.0.0.5")

    def test_nonroot_api_receives_secrets_without_reading_host_environment_file(self):
        api = render()["services"]["api"]
        self.assertTrue(bool(api["environment"].get("JWT_SECRET")))
        self.assertFalse(
            any(mount["target"] == "/app/.env" for mount in api["volumes"])
        )

    def test_default_stack_exposes_only_local_chat(self):
        services = render()["services"]
        self.assertEqual(set(services), {"api", "mongodb", "meilisearch", "rustfs", "rustfs-init"})
        ports = [
            port for service in services.values() for port in service.get("ports", [])
        ]
        self.assertEqual(len(ports), 1)
        self.assertEqual(ports[0]["host_ip"], "127.0.0.1")
        self.assertEqual(str(ports[0]["published"]), "3080")
        self.assertEqual(services["api"]["environment"]["RAG_API_URL"], "")
        self.assertNotIn("9001", str(services["rustfs"]))  # no published port, no web console
        self.assertEqual(services["rustfs"]["environment"]["RUSTFS_CONSOLE_ENABLE"], "false")

    def test_all_images_are_immutable_including_inactive_profiles(self):
        for name, service in render("--profile", "*")["services"].items():
            with self.subTest(service=name):
                if name == "api":
                    self.assertEqual(service["image"], "kai-librechat:0.2.0")
                    self.assertTrue(
                        service["build"]["context"].endswith("/services/platform-api")
                    )
                    dockerfile = (ROOT / "services/platform-api/Dockerfile").read_text()
                    self.assertIn(
                        "FROM registry.librechat.ai/librechat-ai/librechat:v0.8.8@sha256:5b706bcee1ca154a1e2987303aaeca0eb27f97919cccee4bb604226c2f8abf61",
                        dockerfile,
                    )
                    self.assertTrue(dockerfile.rstrip().endswith("USER 1000:1000"))
                    continue
                if name == "admin-panel":
                    self.assertEqual(service["image"], "kai-admin-panel:0.1.0")
                    self.assertIn(
                        "@sha256:a395465d1daa3c11495810a8c224df86675985d50de42e98e36eeedbd8aecb94",
                        (ROOT / "services/admin-panel/Dockerfile").read_text(),
                    )
                    continue
                self.assertRegex(service["image"], r"@sha256:[0-9a-f]{64}$")

    def test_data_uses_named_volumes_and_config_mounts_are_readonly(self):
        services = render()["services"]
        for name, target in (
            ("mongodb", "/data/db"),
            ("meilisearch", "/meili_data"),
            ("api", "/app/uploads"),
        ):
            mount = next(
                item for item in services[name]["volumes"] if item["target"] == target
            )
            self.assertEqual(mount["type"], "volume")
        for mount in services["api"]["volumes"]:
            if mount["type"] == "bind":
                self.assertTrue(mount["read_only"])

    def test_docker_logs_are_bounded_for_all_profiles(self):
        for name, service in render("--profile", "*")["services"].items():
            with self.subTest(service=name):
                self.assertEqual(
                    service["logging"],
                    {
                        "driver": "local",
                        "options": {"max-size": "10m", "max-file": "3"},
                    },
                )


if __name__ == "__main__":
    unittest.main()
