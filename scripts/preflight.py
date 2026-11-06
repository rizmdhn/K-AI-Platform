"""Server preflight for the Make path (`make preflight`): is this host ready to run kAI?

Checks tools, Docker/Compose versions, CPU arch, memory, disk, free ports, outbound access to every
registry/provider the build and runtime need, ClickHouse reachability (when MCP is on) and the root
.env. Prints PASS/WARN/FAIL per check and never prints secret values. Exit code 1 if anything FAILs.
"""
import json
import re
import os
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from bootstrap import settings, value

ROOT = Path(__file__).resolve().parents[1]
MIN_ENGINE, MIN_COMPOSE = (28, 0, 0), (2, 24, 4)
MIN_DISK_GB, MIN_MEM_GB, WANT_MEM_GB = 40, 4, 8
PORTS = (3080, 3081)
# Build pulls (images, pip, npm, panel source), first-start model download, runtime model provider.
EGRESS = {
    "Docker Hub": "https://registry-1.docker.io/v2/",
    "GHCR (llama.cpp)": "https://ghcr.io/v2/",
    "LibreChat registry": "https://registry.librechat.ai/v2/",
    "PyPI (MCP build)": "https://pypi.org/simple/",
    "npm (Admin Panel build)": "https://registry.npmjs.org/",
    "GitHub (Admin Panel source)": "https://github.com/",
    "Hugging Face (bge-m3 model)": "https://huggingface.co/",
    "Ollama Cloud (chat model)": "https://ollama.com/",
}
REQUIRED_SECRETS = ("CREDS_KEY", "CREDS_IV", "JWT_SECRET", "JWT_REFRESH_SECRET", "MEILI_MASTER_KEY",
                    "POSTGRES_PASSWORD", "ADMIN_PANEL_SESSION_SECRET", "KAI_S3_ACCESS_KEY",
                    "KAI_S3_SECRET_KEY", "KAI_S3_APP_ACCESS_KEY", "KAI_S3_APP_SECRET_KEY")


def version(text):
    parts = text.strip().lstrip("v").split("-")[0].split("+")[0].split(".")
    return tuple(int(p) for p in (parts + ["0", "0"])[:3])


def run(*argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


def env_checks(env, mode):
    """Pure checks on the parsed .env; returns (status, name, detail) rows."""
    rows = []
    empty = [k for k in REQUIRED_SECRETS if not value(env.get(k, ""))]
    rows.append(("FAIL", ".env secrets", f"empty: {', '.join(empty)}; run make setup") if empty
                else ("PASS", ".env secrets", f"{len(REQUIRED_SECRETS)} generated secrets present"))
    if mode & 0o077:
        rows.append(("WARN", ".env permissions", f"mode {oct(mode & 0o777)}; use chmod 600 .env"))
    reg = value(env.get("ALLOW_REGISTRATION", "false")).lower()
    rows.append(("FAIL", "self-registration", "ALLOW_REGISTRATION=true; set false, use make admin-create")
                if reg == "true" else ("PASS", "self-registration", "disabled"))
    if value(env.get("KAI_AGENT_PROVIDER", "")) != "google" and not value(env.get("OLLAMA_API_KEY", "")):
        rows.append(("WARN", "chat model key", "OLLAMA_API_KEY empty: chat can use a key set in the Admin Panel"))
    if value(env.get("KAI_BATCH_ENABLED", "false")).lower() == "true":
        batch_key = value(env.get("KAI_BATCH_MODEL_API_KEY", "")) or value(env.get("OLLAMA_API_KEY", ""))
        rows.append(("WARN", "bulk labelling", "KAI_BATCH_ENABLED=true but no OLLAMA_API_KEY/KAI_BATCH_MODEL_API_KEY in .env")
                    if not batch_key else ("PASS", "bulk labelling", "enabled: row text is sent to the model provider"))
    if value(env.get("KAI_MCP_ENABLED", "false")).lower() == "true" and not value(env.get("CLICKHOUSE_HOST", "")):
        rows.append(("FAIL", "ClickHouse", "KAI_MCP_ENABLED=true but CLICKHOUSE_HOST is empty"))
    try:
        days = int(value(env.get("KAI_LOG_RETENTION_DAYS", "") or "90"))
        assert 1 <= days <= 3650
    except (ValueError, AssertionError):
        rows.append(("FAIL", "log retention", "KAI_LOG_RETENTION_DAYS must be 1-3650"))
    return rows


API_UID = 1000  # LibreChat api container user (node)


def vertex_checks(env, path_stat=os.stat, read=lambda p: Path(p).read_text()):
    """Gemini on Vertex AI: settings and the service account file, never printing its contents."""
    key_file = value(env.get("KAI_VERTEX_KEY_FILE", ""))
    provider = value(env.get("KAI_AGENT_PROVIDER", ""))
    if not key_file and provider != "google":
        return []
    rows = []
    if provider != "google":
        rows.append(("FAIL", "inference provider", "KAI_VERTEX_KEY_FILE is set; set KAI_AGENT_PROVIDER=google"))
    for name in ("KAI_VERTEX_LOCATION", "KAI_GENERAL_AGENT_MODEL"):
        if not value(env.get(name, "")):
            rows.append(("FAIL", "Vertex settings", f"{name} is empty"))
    if not key_file:
        return rows + [("FAIL", "Vertex service account", "KAI_VERTEX_KEY_FILE is empty")]
    try:
        st = path_stat(key_file)
        data = json.loads(read(key_file))
        ok = isinstance(data, dict) and data.get("type") == "service_account" and all(
            isinstance(data.get(k), str) and data[k] for k in ("project_id", "client_email", "private_key"))
    except (OSError, ValueError):
        return rows + [("FAIL", "Vertex service account", f"{key_file} missing or not JSON")]
    rows.append(("PASS", "Vertex service account", f"project {data['project_id']}") if ok
                else ("FAIL", "Vertex service account", "not a service account key JSON"))
    readable = (st.st_uid == API_UID and st.st_mode & 0o400) or (st.st_gid == API_UID and st.st_mode & 0o040)
    rows.append(("WARN", "Vertex key permissions", "readable by everyone; use chown root:1000 + chmod 440")
                if st.st_mode & 0o004 else
                ("PASS", "Vertex key permissions", "readable by the api container only") if readable else
                ("FAIL", "Vertex key permissions", f"api (UID {API_UID}) cannot read it; chown root:1000 + chmod 440"))
    return rows


def reachable(url):
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=10)
    except urllib.error.HTTPError:
        pass  # any HTTP answer (401/403/405) proves the path is open
    except OSError as error:
        return str(getattr(error, "reason", error))[:80]
    return None


def clickhouse_ping(host, port):
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=10) as raw, context.wrap_socket(raw, server_hostname=host) as tls:
            tls.sendall(f"GET /ping HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
            return tls.recv(64).decode(errors="replace").split("\r\n")[0]
    except OSError as error:
        return f"error: {str(error)[:80]}"


def main():
    rows = []
    hints = {"docker": "install Docker Engine >= 28 (docs.docker.com/engine/install/ubuntu)",
             "make": "sudo apt-get install -y make", "curl": "sudo apt-get install -y curl"}
    for tool in ("docker", "make", "python3", "curl"):
        rows.append(("PASS", f"tool {tool}", "found") if shutil.which(tool)
                    else ("FAIL", f"tool {tool}", f"missing; {hints.get(tool, 'install it')}"))
    if sys.version_info < (3, 10):
        rows.append(("FAIL", "python", f"{sys.version.split()[0]} < 3.10"))
    info = run("docker", "info", "--format", "{{json .}}") if shutil.which("docker") else None
    if not info or info.returncode:
        rows.append(("FAIL", "docker daemon", "not reachable by this user (docker group or sudo?)"))
    else:
        info = json.loads(info.stdout)
        engine = version(info.get("ServerVersion", "0"))
        rows.append(("PASS" if engine >= MIN_ENGINE else "FAIL", "Docker Engine", f"{info.get('ServerVersion')} (need >= 28.0.0)"))
        compose = run("docker", "compose", "version", "--short")
        rows.append(("PASS" if not compose.returncode and version(compose.stdout) >= MIN_COMPOSE else "FAIL",
                     "Docker Compose", f"{compose.stdout.strip() or 'missing'} (need >= 2.24.4)"))
        arch = info.get("Architecture", "?")
        rows.append(("PASS" if arch in ("x86_64", "aarch64", "amd64", "arm64") else "FAIL", "architecture", arch))
        mem = info.get("MemTotal", 0) / 1024**3
        rows.append(("PASS" if mem >= WANT_MEM_GB else "WARN" if mem >= MIN_MEM_GB else "FAIL", "memory",
                     f"{mem:.1f} GiB (want >= {WANT_MEM_GB}; embeddings + RAG need the most)"))
        root = info.get("DockerRootDir", "")
        if os.path.isdir(root):
            free = shutil.disk_usage(root).free / 1024**3
            rows.append(("PASS" if free >= MIN_DISK_GB else "FAIL", "disk free", f"{free:.0f} GB at {root} (need >= {MIN_DISK_GB})"))
        else:
            rows.append(("WARN", "disk free", "Docker root not visible here (Docker Desktop?); check the VM disk"))
    for port in PORTS:
        with socket.socket() as s:
            busy = s.connect_ex(("127.0.0.1", port)) == 0
        rows.append(("WARN" if busy else "PASS", f"port {port}", "in use (kAI already running?)" if busy else "free"))
    for name, url in EGRESS.items():
        error = reachable(url)
        rows.append(("FAIL" if error else "PASS", f"egress {name}", error or "reachable"))
    path = ROOT / ".env"
    if not path.exists():
        rows.append(("FAIL", ".env", "missing; cp .env.example .env && make setup"))
    else:
        env = settings(path.read_text())
        rows += env_checks(env, stat.S_IMODE(path.stat().st_mode))
        rows += vertex_checks(env)
        location = value(env.get("KAI_VERTEX_LOCATION", ""))
        if value(env.get("KAI_AGENT_PROVIDER", "")) == "google" and re.fullmatch(r"[a-z0-9-]+", location or "-"):
            host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
            for name, url in (("Google OAuth", "https://oauth2.googleapis.com/"), ("Vertex AI", f"https://{host}/")):
                error = reachable(url)
                rows.append(("FAIL" if error else "PASS", f"egress {name}", error or "reachable"))
        host = value(env.get("CLICKHOUSE_HOST", ""))
        if value(env.get("KAI_MCP_ENABLED", "false")).lower() == "true" and host:
            line = clickhouse_ping(host, int(value(env.get("CLICKHOUSE_PORT", "8443")) or 8443))
            ok = " 200 " in f" {line} "
            rows.append(("PASS" if ok else "FAIL", "ClickHouse /ping",
                         line if ok else f"{line}; add this server's public IP to the CH Cloud allowlist / start the service"))
        keys = value(env.get("KAI_BINDING_KEY_SOURCE", ""))
        if keys:
            st = os.stat(keys) if os.path.isdir(keys) else None
            rows.append(("PASS" if st and st.st_uid == 10001 else "FAIL", "binding keyring",
                         "owned by UID 10001" if st and st.st_uid == 10001 else f"{keys} missing or not owned by UID 10001"))
    width = max(len(r[1]) for r in rows)
    for status, name, detail in rows:
        print(f"{status:<4}  {name:<{width}}  {detail}")
    failed = sum(r[0] == "FAIL" for r in rows)
    print(f"\n{failed} FAIL, {sum(r[0] == 'WARN' for r in rows)} WARN")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
