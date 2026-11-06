"""Isolated local PostgreSQL fixture; no app/warehouse database access."""
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
import uuid

DB_DIR = Path(__file__).resolve().parents[1]
IMAGE = "postgres:15@sha256:77cb52de60c9c248e26267a6707db24f8433461ccd60b2c861900dcfe0c5a68e"


class LocalPostgresTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        endpoint = os.environ.get("DOCKER_HOST")
        context = subprocess.run(["docker", "context", "inspect"], check=True, capture_output=True, text=True)
        context_endpoint = json.loads(context.stdout)[0]["Endpoints"]["docker"]["Host"]
        if ((endpoint and not endpoint.startswith("unix://")) or not context_endpoint.startswith("unix://")):
            raise RuntimeError("Migration tests require a local Docker daemon")
        cls.container = "kai-db-test-" + uuid.uuid4().hex[:12]
        cls.socket_volume = None
        if getattr(cls, 'SHARE_SOCKET', False):
            cls.socket_volume = 'kai-db-socket-' + uuid.uuid4().hex[:12]
            subprocess.run(['docker', 'volume', 'create', cls.socket_volume], check=True, capture_output=True, text=True)
            cls.addClassCleanup(lambda: subprocess.run(['docker', 'volume', 'rm', cls.socket_volume],
                                                       check=True, capture_output=True, text=True))
        cls.addClassCleanup(cls.remove_container)
        socket_mount = (['--mount', f'type=volume,src={cls.socket_volume},dst=/var/run/postgresql']
                        if cls.socket_volume else [])
        subprocess.run([
            "docker", "run", "--detach", "--name", cls.container, "--network", "none",
            "--tmpfs", "/var/lib/postgresql/data:rw", "--env", "POSTGRES_HOST_AUTH_METHOD=trust",
            "--mount", f"type=bind,src={DB_DIR},dst=/migrations,readonly", *socket_mount, IMAGE,
        ], check=True, capture_output=True, text=True)
        for _ in range(60):
            # initdb's temporary server accepts SQL before the entrypoint starts the final server.
            started = subprocess.run(["docker", "exec", cls.container, "sh", "-c",
                                      'test "$(cat /proc/1/comm)" = postgres'],
                                     check=False, capture_output=True, text=True)
            if started.returncode == 0 and cls.execute("SELECT 1", check=False).returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Disposable PostgreSQL did not become ready")
        cls.execute("CREATE ROLE kai_migrator NOLOGIN; CREATE ROLE kai_runtime NOLOGIN;")

    @classmethod
    def remove_container(cls):
        subprocess.run(["docker", "rm", "--force", "--volumes", cls.container],
                       check=True, capture_output=True, text=True)

    @classmethod
    def execute(cls, sql, database="postgres", check=True):
        return subprocess.run([
            "docker", "exec", "-i", cls.container, "psql", "-X", "-U", "postgres",
            "-d", database, "-v", "ON_ERROR_STOP=1", "-qAt",
        ], input=sql, check=check, capture_output=True, text=True)

    def setUp(self):
        self.database = "fixture_" + uuid.uuid4().hex[:12]
        self.execute(f"CREATE DATABASE {self.database} OWNER kai_migrator;")
        self.sql("CREATE TABLE public.rag_sentinel (value text);"
                 "INSERT INTO public.rag_sentinel VALUES ('upstream remains intact');")

    def sql(self, text, check=True, role="kai_migrator"):
        # Session authorization prevents the fixture's original superuser login from bypassing SET ROLE tests.
        return self.execute(f"SET SESSION AUTHORIZATION {role};\n{text}", self.database, check)
