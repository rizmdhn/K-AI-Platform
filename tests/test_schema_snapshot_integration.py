"""Opt-in isolated ClickHouse fixture: no host ports, runtime env, Cloud or model calls."""

import json
import os
from pathlib import Path
import subprocess
import time
import unittest
import uuid

IMAGE = os.environ.get("KAI_SCHEMA_TEST_IMAGE")
CH_IMAGE = "clickhouse/clickhouse-server@sha256:0152dd511befe6a2c2ef53e930726179669b08116da78500b37c51c96ff5ee77"
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    IMAGE,
    "set KAI_SCHEMA_TEST_IMAGE to an existing local image with clickhouse-connect",
)
class SchemaSnapshotIntegration(unittest.TestCase):
    def test_real_scoped_readonly_schema_collection(self):
        context = subprocess.run(
            ["docker", "context", "inspect"], check=True, capture_output=True, text=True
        )
        endpoint = json.loads(context.stdout)[0]["Endpoints"]["docker"]["Host"]
        if not endpoint.startswith("unix://") or not os.environ.get(
            "DOCKER_HOST", "unix://"
        ).startswith("unix://"):
            self.fail("Schema fixture requires a local Docker daemon")
        network = "kai-schema-" + uuid.uuid4().hex[:12]
        server, probe = network + "-ch", network + "-probe"

        def run(command, **kwargs):
            return subprocess.run(
                ["docker", *command],
                capture_output=True,
                text=True,
                timeout=150,
                **kwargs,
            )

        result = run(["network", "create", "--internal", network], check=True)
        self.addCleanup(lambda: run(["network", "rm", network], check=True))
        self.addCleanup(lambda: run(["rm", "--force", "--volumes", server], check=True))
        run(
            [
                "run",
                "--detach",
                "--pull",
                "never",
                "--name",
                server,
                "--network",
                network,
                "--network-alias",
                "warehouse",
                "--env",
                "CLICKHOUSE_PASSWORD=fixture-admin-only",
                "--env",
                "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1",
                "--tmpfs",
                "/var/lib/clickhouse",
                "--tmpfs",
                "/var/log/clickhouse-server",
                CH_IMAGE,
            ],
            check=True,
        )

        def sql(text, check=True):
            return run(
                [
                    "exec",
                    "-i",
                    server,
                    "clickhouse-client",
                    "--password",
                    "fixture-admin-only",
                    "--multiquery",
                ],
                input=text,
                check=check,
            )

        for _ in range(90):
            if sql("SELECT 1", check=False).returncode == 0:
                break
            time.sleep(0.5)
        else:
            self.fail("Disposable ClickHouse did not become ready")
        sql("""CREATE DATABASE sales;
CREATE TABLE sales.orders(region String COMMENT 'Wilayah', amount UInt32) ENGINE=Memory COMMENT 'Pesanan';
CREATE TABLE sales.secret(secret String) ENGINE=Memory;
CREATE TABLE sales.`order_%\\\\\\`'; DROP TABLE x;--`(literal String) ENGINE=Memory;
CREATE USER ingest IDENTIFIED WITH plaintext_password BY 'fixture-only';
CREATE USER other IDENTIFIED WITH plaintext_password BY 'fixture-only';
CREATE USER columns_only IDENTIFIED WITH plaintext_password BY 'fixture-only';
GRANT SELECT, INSERT ON sales.orders TO ingest;
GRANT SELECT ON sales.`order_%\\\\\\`'; DROP TABLE x;--` TO ingest;
GRANT SELECT ON sales.secret TO other;
GRANT SELECT(region) ON sales.orders TO columns_only;
""")
        # Match the effective readonly profile; immutable settings are never silently relaxed.
        settings = (
            "readonly=1,max_execution_time=10,max_result_rows=4097,max_result_bytes=1048576,"
            "max_memory_usage=67108864,max_rows_to_read=1000000,max_bytes_to_read=67108864,"
            "result_overflow_mode='throw',timeout_overflow_mode='throw',read_overflow_mode='throw'"
        )
        for user in ("ingest", "other", "columns_only"):
            sql(f"ALTER USER {user} SETTINGS {settings}")
        self.addCleanup(lambda: run(["rm", "--force", probe], check=False))
        result = run(
            [
                "run",
                "--rm",
                "--pull",
                "never",
                "--name",
                probe,
                "--network",
                network,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--tmpfs",
                "/tmp",
                "--user",
                "10001:10001",
                "--entrypoint",
                "python",
                "--mount",
                f'type=bind,src={ROOT / "services/metadata-worker"},dst=/worker,readonly',
                IMAGE,
                "/worker/tests/probe_schema_snapshot.py",
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 7 tests", result.stderr)


if __name__ == "__main__":
    unittest.main()
