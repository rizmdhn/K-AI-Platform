"""Opt-in HTTPS integration check; uses and removes only a unique temporary stack."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]


def wait_for(operation):
    deadline = time.monotonic() + 45
    while True:
        try:
            return operation()
        except (OSError, urllib.error.URLError, subprocess.CalledProcessError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def main():
    host = os.environ.get("DOCKER_HOST", "")
    if host and not host.startswith(("unix://", "npipe://")):
        raise RuntimeError("TLS smoke test requires a local Docker socket")
    context = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    if not json.loads(context.stdout).startswith(("unix://", "npipe://")):
        raise RuntimeError("TLS smoke test requires a local Docker context")
    project = "kai-tls-smoke-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix=project + "-") as temporary:
        stage = Path(temporary)
        deploy = stage / "deploy"
        for relative in (
            "upstream/docker-compose.yml",
            "docker-compose.override.yml",
            "docker-compose.tls.yml",
            "librechat.yaml",
            "proxy/Caddyfile",
        ):
            target = deploy / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "deploy" / relative, target)
        spec = importlib.util.spec_from_file_location(
            "bootstrap", ROOT / "scripts/bootstrap.py"
        )
        bootstrap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bootstrap)
        bootstrap.initialize(stage)
        extra = stage / "smoke.yml"
        extra.write_text(
            "services:\n"
            "  reverse-proxy:\n    ports: !override ['127.0.0.1:0:443/tcp']\n"
        )
        prefix = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--project-directory",
            str(deploy),
            "--env-file",
            str(stage / ".env"),
            "-f",
            str(deploy / "upstream/docker-compose.yml"),
            "-f",
            str(deploy / "docker-compose.override.yml"),
            "-f",
            str(deploy / "docker-compose.tls.yml"),
            "-f",
            str(extra),
        ]
        environment = dict(os.environ)
        environment.update(
            dict(
                line.split("=", 1) for line in (stage / ".env").read_text().splitlines()
            )
        )
        environment["KAI_DOMAIN"] = "localhost"

        def compose(*arguments):
            return subprocess.run(
                prefix + list(arguments),
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )

        config = json.loads(compose("config", "--format", "json").stdout)
        assert set(config["services"]) == {
            "api",
            "mongodb",
            "meilisearch",
            "reverse-proxy",
        }
        for kind in ("volumes", "networks"):
            for item in config.get(kind, {}).values():
                assert not item.get(
                    "external"
                ), "Smoke stack must not use external resources"
                assert item["name"].startswith(
                    project + "_"
                ), "Smoke resource is not isolated"
        for service in config["services"].values():
            for port in service.get("ports", []):
                assert (
                    port["host_ip"] == "127.0.0.1"
                ), "Smoke ports must stay on loopback"
        try:
            print("Starting isolated TLS stack:", project, flush=True)
            compose("up", "-d", "--wait", "--wait-timeout", "180")
            address = compose("port", "reverse-proxy", "443").stdout.strip()
            assert address.startswith("127.0.0.1:")
            port = int(address.rsplit(":", 1)[1])
            origin = f"https://localhost:{port}"
            certificate = stage / "root.crt"

            def copy_root():
                compose(
                    "cp",
                    "reverse-proxy:/data/caddy/pki/authorities/local/root.crt",
                    str(certificate),
                )

            wait_for(copy_root)
            context = ssl.create_default_context(cafile=str(certificate))

            def request(path):
                with urllib.request.urlopen(
                    origin + path, context=context, timeout=5
                ) as response:
                    assert response.status == 200
                    return response.read()

            wait_for(lambda: request("/health"))
            assert b"<html" in request("/login").lower()
            configuration = json.loads(request("/api/config"))
            assert configuration["appTitle"] == "kAI"
            assert configuration["registrationEnabled"] is False
            try:
                urllib.request.urlopen(origin + "/health", timeout=5)
            except urllib.error.URLError as error:
                assert isinstance(error.reason, ssl.SSLCertVerificationError)
            else:
                raise AssertionError(
                    "Local CA must not be added to the system trust store"
                )
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5)
            except urllib.error.HTTPError as error:
                assert error.code == 400
            else:
                raise AssertionError(
                    "TLS listener unexpectedly accepted plaintext HTTP"
                )
            root_before = certificate.read_bytes()
            compose(
                "up", "-d", "--no-deps", "--force-recreate", "--wait", "reverse-proxy"
            )
            # Docker can allocate a different random host port after recreation.
            address = compose("port", "reverse-proxy", "443").stdout.strip()
            port = int(address.rsplit(":", 1)[1])
            origin = f"https://localhost:{port}"
            wait_for(copy_root)
            assert (
                certificate.read_bytes() == root_before
            ), "Proxy recreation replaced its CA"
            wait_for(lambda: request("/health"))
            print(
                "HTTPS health/login/config verified with certificate and hostname validation.",
                flush=True,
            )
            print(
                "Untrusted CA and plaintext HTTP rejected; CA persisted across proxy recreation.",
                flush=True,
            )
        finally:
            compose("down", "--volumes", "--remove-orphans")
            print(
                "Removed isolated smoke containers, network and volumes:",
                project,
                flush=True,
            )


if __name__ == "__main__":
    main()
