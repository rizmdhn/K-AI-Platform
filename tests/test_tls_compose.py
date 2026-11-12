import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def render_tls(domain="chat.example.com", all_profiles=False, port_env=None):
    environment = dict(os.environ, PORT="3080", **(port_env or {}))
    environment.pop("KAI_DOMAIN", None)
    if domain is not None:
        environment["KAI_DOMAIN"] = domain
    with tempfile.TemporaryDirectory() as directory:
        variables = Path(directory) / "compose.env"
        variables.write_text(
            "PORT=3080\nUID=1000\nGID=1000\nPOSTGRES_DB=test\n"
            "POSTGRES_USER=test\nPOSTGRES_PASSWORD=test\n"
        )
        command = [
            "docker",
            "compose",
            "--project-name",
            "kai",
            "--project-directory",
            "deploy",
            "--env-file",
            str(variables),
            "-f",
            "deploy/upstream/docker-compose.yml",
            "-f",
            "deploy/docker-compose.override.yml",
            "-f",
            "deploy/docker-compose.tls.yml",
        ]
        if all_profiles:
            command += ["--profile", "*"]
        return subprocess.run(
            command + ["config", "--format", "json"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )


class TLSComposeTests(unittest.TestCase):
    def deployment(self, all_profiles=False):
        result = render_tls(all_profiles=all_profiles)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_only_proxy_publishes_chat_443_and_admin_8443_even_with_optional_profiles(self):
        services = self.deployment(all_profiles=True)["services"]
        published = [
            (name, port)
            for name, service in services.items()
            for port in service.get("ports", [])
            if port.get("host_ip") != "127.0.0.1"
        ]
        # Only the Admin Panel keeps a loopback port, for SSH tunnels.
        loopback = {name for name, service in services.items()
                    for port in service.get("ports", []) if port.get("host_ip") == "127.0.0.1"}
        self.assertEqual(loopback, {"admin-panel"})
        self.assertEqual({name for name, _ in published}, {"reverse-proxy"})
        self.assertEqual(sorted((str(p["published"]), p["target"], p["host_ip"], p["protocol"]) for _, p in published),
                         [("443", 443, "0.0.0.0", "tcp"), ("8443", 8443, "0.0.0.0", "tcp")])

    def test_admin_panel_behind_proxy_uses_secure_session_and_https_url(self):
        services = self.deployment(all_profiles=True)["services"]
        self.assertEqual(services["admin-panel"]["environment"]["SESSION_COOKIE_SECURE"], "true")
        self.assertIn("web", services["admin-panel"]["networks"])
        self.assertEqual(services["api"]["environment"]["ADMIN_PANEL_URL"], "https://chat.example.com:8443")

    def test_admin_port_can_move_when_8443_is_taken(self):
        result = render_tls(port_env={"KAI_ADMIN_HTTPS_PORT": "9443"})
        self.assertEqual(result.returncode, 0, result.stderr)
        services = json.loads(result.stdout)["services"]
        self.assertIn("9443", {str(p["published"]) for p in services["reverse-proxy"]["ports"]})
        self.assertEqual(services["api"]["environment"]["ADMIN_PANEL_URL"], "https://chat.example.com:9443")

    def test_application_uses_https_origins_and_secure_cookies(self):
        environment = self.deployment()["services"]["api"]["environment"]
        self.assertEqual(environment["DOMAIN_CLIENT"], "https://chat.example.com")
        self.assertEqual(environment["DOMAIN_SERVER"], "https://chat.example.com")
        self.assertEqual(environment["SESSION_COOKIE_SECURE"], "true")
        self.assertEqual(environment["TRUST_PROXY"], "1")

    def test_development_signup_is_not_exposed_by_tls(self):
        environment = self.deployment()["services"]["api"]["environment"]
        self.assertEqual(environment["ALLOW_REGISTRATION"], "false")

    def test_proxy_is_separate_from_database_network_and_keeps_certificates(self):
        services = self.deployment()["services"]
        proxy = services["reverse-proxy"]
        self.assertEqual(set(proxy["networks"]), {"web"})
        self.assertTrue({"default", "web"}.issubset(services["api"]["networks"]))
        self.assertNotIn("web", services["mongodb"]["networks"])
        data = next(mount for mount in proxy["volumes"] if mount["target"] == "/data")
        self.assertEqual(data["type"], "volume")
        config = next(
            mount
            for mount in proxy["volumes"]
            if mount["target"] == "/etc/caddy/Caddyfile"
        )
        self.assertTrue(config["read_only"])
        self.assertRegex(proxy["image"], r"@sha256:[0-9a-f]{64}$")

    def test_missing_domain_fails_before_starting_containers(self):
        result = render_tls(domain=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("KAI_DOMAIN", result.stderr)


if __name__ == "__main__":
    unittest.main()
