"""Opt-in clean-install check from public sources; temporary accounts, no CH/model calls."""

import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
PHASE = "preflight"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    global PHASE
    # Do not inherit host model/warehouse credentials or exported Compose inputs.
    environment = {
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
    }
    endpoint = environment.get("DOCKER_HOST", "")
    if endpoint and not endpoint.startswith(("unix://", "npipe://")):
        raise RuntimeError("A local Docker endpoint is required.")

    def docker(*args, **kwargs):
        return subprocess.run(
            ["docker", *args],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            **kwargs
        )

    context = docker(
        "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"
    )
    if not json.loads(context.stdout).startswith(("unix://", "npipe://")):
        raise RuntimeError("A local Docker context is required.")
    project = "kai-config-smoke-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix=project + "-") as temporary:
        stage = Path(temporary) / "App"
        stage.mkdir()
        release = load("kai_package", ROOT / "scripts/package_release.py")
        archive = Path(temporary) / "source.tar.gz"
        release.package(ROOT, archive)
        release.verify(archive)
        # This archive was generated and verified here; its allowlist forbids traversal and links.
        with tarfile.open(archive) as source:
            source.extractall(stage)
        shutil.copyfile(stage / ".env.example", stage / ".env")
        bootstrap = load("kai_fixture_bootstrap", stage / "scripts/bootstrap.py")
        bootstrap.initialize(stage)
        assert not bootstrap.mcp_enabled(
            bootstrap.settings((stage / ".env").read_text())
        )
        assert not bootstrap.value(
            bootstrap.settings((stage / ".env").read_text()).get("OLLAMA_API_KEY", "")
        )
        extra = stage / "smoke.yml"

        def ports(api="0", admin="0"):
            extra.write_text(
                "services:\n  api:\n    ports: !override ['127.0.0.1:"
                + api
                + ":3080']\n  admin-panel:\n    ports: !override ['127.0.0.1:"
                + admin
                + ":3000']\n"
            )

        ports()

        def compose(*args, enabled=None):
            active = (
                bootstrap.mcp_enabled(bootstrap.settings((stage / ".env").read_text()))
                if enabled is None
                else enabled
            )
            arguments = [
                "compose",
                "--project-name",
                project,
                "--project-directory",
                str(stage / "deploy"),
                "--env-file",
                str(stage / ".env"),
                "-f",
                str(stage / "deploy/upstream/docker-compose.yml"),
                "-f",
                str(stage / "deploy/docker-compose.override.yml"),
            ]
            if active:
                arguments += ["-f", str(stage / "deploy/docker-compose.mcp.yml")]
            arguments += ["-f", str(extra), "--profile", "admin", *args]
            return docker(*arguments)

        initial = json.loads(compose("config", "--format", "json").stdout)
        assert set(initial["services"]) == {
            "api",
            "mongodb",
            "meilisearch",
            "admin-panel",
        }
        load("kai_fixture_mcp", stage / "scripts/configure_mcp.py").configure(
            stage, "fixture.example.invalid", 8443
        )
        load("kai_fixture_metrics", stage / "scripts/configure_metrics.py").configure(
            stage
        )
        config = json.loads(compose("config", "--format", "json").stdout)
        assert set(config["services"]) == {
            "api",
            "mongodb",
            "meilisearch",
            "admin-panel",
            "clickhouse-mcp",
            "metadata-worker",  # idle unless KAI_CATALOG_ENABLED=true
            "vertex-proxy",  # answers 503 until KAI_VERTEX_API_KEY and KAI_VERTEX_PROJECT_ID are set
        }
        for kind in ("volumes", "networks"):
            for resource in config.get(kind, {}).values():
                assert not resource.get("external") and resource["name"].startswith(
                    project + "_"
                )
        for service in config["services"].values():
            for port in service.get("ports", []):
                assert port["host_ip"] == "127.0.0.1"
        # Reuse only prebuilt local images; this check never builds/pulls or touches an active project.
        for service in config["services"].values():
            docker("image", "inspect", service["image"])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def request(base, path, body=None, token=None, form=False):
            headers = {
                "Content-Type": (
                    "application/x-www-form-urlencoded" if form else "application/json"
                ),
                "Origin": base,
                "User-Agent": "Mozilla/5.0 Chrome/131.0.0.0 Safari/537.36",
            }
            if token:
                headers["Authorization"] = "Bearer " + token
            data = (
                None
                if body is None
                else (
                    urllib.parse.urlencode(body).encode()
                    if form
                    else json.dumps(body).encode()
                )
            )
            req = urllib.request.Request(base + path, data=data, headers=headers)
            try:
                with opener.open(req, timeout=15) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as error:
                return error.code, error.read()

        try:
            print("Starting clean-install fixture:", project, flush=True)
            PHASE = "initial five-service startup"
            compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "180")
            api_port = compose("port", "api", "3080").stdout.strip().rsplit(":", 1)[1]
            admin_port = (
                compose("port", "admin-panel", "3000").stdout.strip().rsplit(":", 1)[1]
            )
            base, panel = (
                "http://localhost:" + api_port,
                "http://localhost:" + admin_port,
            )
            # Pin the assigned ports before recreating the origins, so activation URLs match the listener.
            ports(api_port, admin_port)
            PHASE = "managed USER baseline"
            compose("exec", "-T", "api", "node", "config/kai-managed-chat.cjs")
            bootstrap.update_configuration(
                stage,
                lambda fields: {
                    "DOMAIN_CLIENT": base,
                    "DOMAIN_SERVER": base,
                    "ADMIN_PANEL_URL": panel,
                },
            )
            PHASE = "origin and port reconfiguration"
            compose(
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--wait",
                "--wait-timeout",
                "180",
                "api",
                "admin-panel",
            )
            PHASE = "public HTTP checks"
            assert request(base, "/health")[0] == 200
            assert request(panel, "/health")[0] == 200
            assert request(base, "/api/models")[0] == 401
            admin_email = "admin-" + uuid.uuid4().hex + "@example.invalid"
            admin_password = secrets.token_hex(20)
            PHASE = "first admin registration"
            status, _ = request(
                base,
                "/api/auth/register",
                {
                    "name": "Temporary clean-install admin",
                    "email": admin_email,
                    "password": admin_password,
                    "confirm_password": admin_password,
                    "username": "fixture-" + uuid.uuid4().hex[:12],
                },
            )
            assert status == 200, "First admin registration failed"
            PHASE = "admin login"
            status, login = request(
                base,
                "/api/auth/login",
                {"email": admin_email, "password": admin_password},
            )
            assert status == 200
            admin = json.loads(login)
            assert admin["user"]["role"] == "ADMIN"
            # Follow the documented bootstrap: close self-signup after the first admin exists.
            PHASE = "close signup and reapply USER baseline"
            bootstrap.update_configuration(
                stage,
                lambda fields: {
                    "ALLOW_REGISTRATION": "false",
                    "ALLOW_SOCIAL_REGISTRATION": "false",
                },
            )
            compose("exec", "-T", "api", "node", "config/kai-managed-chat.cjs")
            compose(
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--wait",
                "--wait-timeout",
                "180",
                "api",
            )
            assert (
                json.loads(request(base, "/api/config")[1])["registrationEnabled"]
                is False
            )
            email = "user-" + uuid.uuid4().hex + "@example.invalid"
            PHASE = "provision user"
            status, created = request(
                base,
                "/api/admin/users",
                {
                    "name": "Temporary clean-install user",
                    "email": email,
                    "role": "USER",
                },
                admin["token"],
            )
            assert status == 200
            invitation = json.loads(created)
            link = urllib.parse.urlsplit(invitation["activationUrl"])
            assert link.scheme + "://" + link.netloc == base
            secret = urllib.parse.parse_qs(link.fragment)
            password = secrets.token_hex(20)
            PHASE = "activate user"
            status, page = request(
                base,
                "/activate-account",
                {
                    "userId": secret["userId"][0],
                    "token": secret["token"][0],
                    "password": password,
                    "confirm_password": password,
                },
                form=True,
            )
            assert status == 200 and b"Account activated" in page
            PHASE = "user login and denials"
            status, login = request(
                base, "/api/auth/login", {"email": email, "password": password}
            )
            assert status == 200
            user = json.loads(login)
            PHASE = "ordinary user role"
            assert user["user"]["role"] == "USER"
            PHASE = "ordinary user admin denial"
            assert request(base, "/api/admin/users", token=user["token"])[0] == 403
            PHASE = "ordinary user MCP denial"
            assert request(base, "/api/mcp/servers", token=user["token"])[0] == 403
            PHASE = "ordinary user agent creation denial"
            assert request(base, "/api/agents", {}, user["token"])[0] == 403
            # Verify transport discovery/authentication without calling a warehouse tool.
            probe = """const assert=require('node:assert/strict');(async()=>{for(const [token,status] of [['wrong-fixture-token',401],[process.env.CLICKHOUSE_MCP_AUTH_TOKEN,200]]){const r=await fetch('http://clickhouse-mcp:8000/mcp',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json',Accept:'application/json, text/event-stream'},body:JSON.stringify({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-03-26',capabilities:{},clientInfo:{name:'clean-install',version:'1.0'}}}),signal:AbortSignal.timeout(10000)});assert.equal(r.status,status);const session=r.headers.get('mcp-session-id');await r.body?.cancel();if(session)await fetch('http://clickhouse-mcp:8000/mcp',{method:'DELETE',headers:{Authorization:'Bearer '+token,'Mcp-Session-Id':session},signal:AbortSignal.timeout(5000)})}})().then(()=>process.exit(0),()=>process.exit(1));"""
            PHASE = "private MCP handshake"
            compose("exec", "-T", "api", "node", "-e", probe)
            PHASE = "private metrics"
            compose("exec", "-T", "clickhouse-mcp", "python", "-m", "gateway_metrics")
            assert not (stage / "deploy/runtime/mcp.env").exists()
            assert not (stage / "deploy/runtime/metrics.env").exists()
            print(
                "Clean-install root template, default MCP-off, configured five-service startup, admin registration, user activation/login, USER denials, private MCP auth and metrics passed.",
                flush=True,
            )
            print(
                "No model or warehouse tool/query called; no real credentials used.",
                flush=True,
            )
        finally:
            completed_phase = PHASE
            PHASE = "fixture cleanup"
            compose("down", "--volumes", "--remove-orphans", "--timeout", "15")
            print(
                "Removed only fixture containers/networks/volumes:", project, flush=True
            )
            PHASE = completed_phase


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Subprocess/config responses contain fixture credentials; never echo their raw output.
        raise SystemExit(
            "Clean-install fixture failed at "
            + PHASE
            + " ("
            + type(error).__name__
            + "); raw credential-bearing output suppressed. Inspect the phase and explicit cleanup messages."
        ) from None
