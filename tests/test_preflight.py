import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import preflight  # noqa: E402


class PreflightTests(unittest.TestCase):
    def test_version_parsing_handles_docker_formats(self):
        self.assertEqual(preflight.version("28.5.1"), (28, 5, 1))
        self.assertEqual(preflight.version("v2.40.3-desktop.1"), (2, 40, 3))
        self.assertLess(preflight.version("2.24"), preflight.MIN_COMPOSE)

    def test_env_checks_flag_missing_secrets_and_open_registration(self):
        env = {k: "x" for k in preflight.REQUIRED_SECRETS} | {"ALLOW_REGISTRATION": "true", "OLLAMA_API_KEY": "k"}
        env["KAI_S3_APP_SECRET_KEY"] = ""
        rows = {name: (status, detail) for status, name, detail in preflight.env_checks(env, 0o644)}
        self.assertEqual(rows[".env secrets"][0], "FAIL")
        self.assertIn("KAI_S3_APP_SECRET_KEY", rows[".env secrets"][1])
        self.assertEqual(rows["self-registration"][0], "FAIL")
        self.assertEqual(rows[".env permissions"][0], "WARN")

    def test_ready_env_passes(self):
        env = {k: "x" for k in preflight.REQUIRED_SECRETS} | {"ALLOW_REGISTRATION": "false", "OLLAMA_API_KEY": "k",
                                                             "KAI_MCP_ENABLED": "true", "CLICKHOUSE_HOST": "h"}
        self.assertTrue(all(status == "PASS" for status, _, _ in preflight.env_checks(env, 0o600)))

    def test_bulk_labelling_is_reported_only_when_enabled(self):
        base = {k: "x" for k in preflight.REQUIRED_SECRETS} | {"ALLOW_REGISTRATION": "false"}
        rows = lambda env: {name: status for status, name, _ in preflight.env_checks(base | env, 0o600)}
        self.assertNotIn("bulk labelling", rows({"OLLAMA_API_KEY": "k"}), "off by default, even with a chat key")
        self.assertEqual(rows({"KAI_BATCH_ENABLED": "true"})["bulk labelling"], "WARN")
        self.assertEqual(rows({"KAI_BATCH_ENABLED": "true", "OLLAMA_API_KEY": "k"})["bulk labelling"], "PASS")

    def test_secret_values_never_appear_in_output(self):
        env = {k: "S3CR3T-VALUE" for k in preflight.REQUIRED_SECRETS}
        self.assertNotIn("S3CR3T-VALUE", repr(preflight.env_checks(env, 0o600)))


class VertexPreflightTests(unittest.TestCase):
    KEY = '{"type":"service_account","project_id":"proj-demo","client_email":"sa@proj-demo.iam","private_key":"PRIVATE-KEY-VALUE"}'

    def rows(self, env, mode=0o100440, uid=0, gid=1000, text=KEY):
        st = type("S", (), {"st_mode": mode, "st_uid": uid, "st_gid": gid})()
        return {name: (status, detail) for status, name, detail in
                preflight.vertex_checks(env, path_stat=lambda p: st, read=lambda p: text)}

    def test_not_configured_means_no_rows(self):
        self.assertEqual(self.rows({}), {})

    def test_ready_vertex_passes_and_never_prints_the_key(self):
        env = {"KAI_AGENT_PROVIDER": "google", "KAI_VERTEX_KEY_FILE": "/etc/kai/v.json",
               "KAI_VERTEX_LOCATION": "asia-southeast2", "KAI_GENERAL_AGENT_MODEL": "m"}
        rows = self.rows(env)
        self.assertTrue(all(status == "PASS" for status, _ in rows.values()), rows)
        self.assertNotIn("PRIVATE-KEY-VALUE", repr(rows))

    def test_unreadable_world_readable_and_wrong_files_are_flagged(self):
        env = {"KAI_AGENT_PROVIDER": "google", "KAI_VERTEX_KEY_FILE": "/etc/kai/v.json",
               "KAI_VERTEX_LOCATION": "global", "KAI_GENERAL_AGENT_MODEL": "m"}
        self.assertEqual(self.rows(env, mode=0o100400, uid=0, gid=0)["Vertex key permissions"][0], "FAIL")
        self.assertEqual(self.rows(env, mode=0o100444)["Vertex key permissions"][0], "WARN")
        self.assertEqual(self.rows(env, text='{"type":"authorized_user"}')["Vertex service account"][0], "FAIL")
        self.assertEqual(self.rows({**env, "KAI_GENERAL_AGENT_MODEL": ""})["Vertex settings"][0], "FAIL")
        self.assertEqual(self.rows({"KAI_VERTEX_KEY_FILE": "/etc/kai/v.json"})["inference provider"][0], "FAIL")


if __name__ == "__main__":
    unittest.main()
