"""Optional native ACL integration. Disposable local Mongo; no network or real env."""

import json
import os
from pathlib import Path
import subprocess
import time
import unittest
import uuid

IMAGE = os.environ.get("KAI_AGENT_ACL_TEST_IMAGE")


@unittest.skipUnless(IMAGE, "set KAI_AGENT_ACL_TEST_IMAGE for isolated native ACL test")
class NativeAgentACL(unittest.TestCase):
    def test_assignment_and_revocation_use_native_database_permissions(self):
        context = json.loads(self.run_docker("context", "inspect").stdout)[0]
        if not context["Endpoints"]["docker"]["Host"].startswith("unix://") or (
            os.environ.get("DOCKER_HOST")
            and not os.environ["DOCKER_HOST"].startswith("unix://")
        ):
            self.fail("Local Docker required")
        name = "kai-agent-acl-" + uuid.uuid4().hex[:12]
        volume = name + "-socket"
        self.run_docker("volume", "create", volume)
        self.addCleanup(self.run_docker, "volume", "rm", volume)
        self.addCleanup(self.run_docker, "rm", "-f", "-v", name)
        self.run_docker(
            "run",
            "-d",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "none",
            "--user",
            "0:0",
            "--tmpfs",
            "/data/db",
            "--tmpfs",
            "/data/configdb",
            "--mount",
            f"type=volume,src={volume},dst=/socket",
            "--entrypoint",
            "mongod",
            "mongo:8.0.20@sha256:098862b1339f031900ca66cf8fef799e616d6324fa41b9a263f2ec899552c1ef",
            "--bind_ip",
            "127.0.0.1",
            "--unixSocketPrefix",
            "/socket",
            "--filePermissions",
            "0777",
        )
        for _ in range(30):
            result = self.run_docker(
                "exec",
                name,
                "mongosh",
                "--quiet",
                "--eval",
                "db.runCommand({ping:1}).ok",
                check=False,
            )
            if result.returncode == 0:
                break
            time.sleep(0.5)
        else:
            self.fail("Disposable Mongo did not become ready")
        probe = Path(__file__).with_name("probe_agent_acl_database.cjs")
        result = self.run_docker(
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/app/logs:rw,mode=1777",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=volume,src={volume},dst=/socket,readonly",
            "--mount",
            f"type=bind,src={probe},dst=/app/probe_agent_acl_database.cjs,readonly",
            "--entrypoint",
            "node",
            IMAGE,
            "/app/probe_agent_acl_database.cjs",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Native Mongo:", result.stdout)

    @staticmethod
    def run_docker(*args, check=True):
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, check=check, timeout=60
        )
