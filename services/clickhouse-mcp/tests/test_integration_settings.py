"""Admin Panel integration settings without PostgreSQL: validation, token encryption, env fallback, route auth."""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import integration_settings as settings

FORM = {"url": "https://om.example.co.id", "service": "clickhouse_prod", "token": "bot-secret",
        "catalog_enabled": True, "databases": ["gold", "silver", "gold"], "scan_hours": 12, "max_tables": 20}


class Request:
    def __init__(self, body=None, method="GET", token="internal"):
        self.method, self.headers = method, ({"authorization": f"Bearer {token}"} if token else {})
        self._body = json.dumps(body or {}).encode()

    async def body(self):
        return self._body


class IntegrationSettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.mkdtemp()
        os.chmod(directory, 0o700)
        with open(os.path.join(directory, "1.key"), "wb") as handle:
            handle.write(os.urandom(32))
        os.chmod(os.path.join(directory, "1.key"), 0o600)
        self.env = {"KAI_BINDING_KEY_DIR": directory, "KAI_BINDING_ACTIVE_KEY_VERSION": "1"}
        self.addCleanup(settings._current.update, at=-60.0, values={})

    def test_form_is_validated_and_normalised(self):
        clean = settings.clean(FORM, {})
        self.assertEqual(clean["KAI_CATALOG_DATABASES"], "gold,silver")
        self.assertEqual((clean["KAI_CATALOG_ENABLED"], clean["KAI_CATALOG_SCAN_HOURS"]), ("true", "12"))
        for change, message in [({"service": ""}, "service"), ({"token": ""}, "token"), ({"url": "ftp://x"}, "URL"),
                                ({"ca_cert": "not a pem"}, "PEM"), ({"databases": ["gold; DROP"]}, "database"),
                                ({"scan_hours": 0}, "Interval"), ({"max_tables": "many"}, "Batas"),
                                ({"url": "", "catalog_enabled": True}, "URL")]:
            with self.subTest(change=change), self.assertRaisesRegex(settings.SettingsError, message):
                settings.clean({**FORM, **change}, {})

    def test_empty_token_keeps_the_saved_one_and_no_url_clears_it(self):
        self.assertEqual(settings.clean({**FORM, "token": ""}, {settings.SECRET: "saved"})[settings.SECRET], "saved")
        cleared = settings.clean({"url": "", "catalog_enabled": False}, {settings.SECRET: "saved"})
        self.assertEqual((cleared["KAI_OPENMETADATA_URL"], cleared[settings.SECRET]), ("", ""))

    def test_saved_token_is_encrypted_and_never_public(self):
        stored = {}

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params):
                stored["params"] = params

        with mock.patch.object(settings, "_connect", lambda env: Connection()):
            settings.save(settings.clean(FORM, {}), "a" * 24, self.env)
        name, config, secret, version, by = stored["params"]
        self.assertNotIn(b"bot-secret", secret)
        self.assertNotIn("bot-secret", config)
        row = (json.loads(config), secret, version, by, datetime(2026, 11, 10, tzinfo=timezone.utc))
        value = settings.effective(self.env, row=row)
        self.assertEqual((value[settings.SECRET], value["source"]), ("bot-secret", "ui"))
        public = settings.public(value, row)
        self.assertNotIn("bot-secret", json.dumps(public))
        self.assertTrue(public["token_set"])
        self.assertEqual(public["settings"]["databases"], ["gold", "silver"])

    def test_without_saved_settings_env_is_used(self):
        env = {**self.env, "KAI_OPENMETADATA_URL": "https://om.example", "KAI_OPENMETADATA_TOKEN": "t"}
        self.assertEqual(settings.effective(env, row=None)["source"], "env")
        self.assertEqual(settings.effective(self.env, row=None)["source"], "none")

    def test_routes_need_the_internal_token_and_storage(self):
        with mock.patch.dict(os.environ, {"KAI_PLATFORM_INTERNAL_TOKEN": "internal", "KAI_CATALOG_DSN": ""}):
            for handler in (settings.settings_request, settings.test_request):
                self.assertEqual(asyncio.run(handler(Request(token="wrong"))).status_code, 401)
            response = asyncio.run(settings.settings_request(Request({**FORM, "updated_by": "a" * 24}, "PUT")))
            self.assertEqual(response.status_code, 400)
            self.assertIn("warehouse-local-setup", json.loads(response.body)["message"])
            self.assertEqual(asyncio.run(settings.settings_request(Request({**FORM, "updated_by": "x"}, "PUT"))).status_code, 400)

    def test_batch_model_key_is_encrypted_kept_and_hidden(self):
        form = {"enabled": True, "base_url": "https://model.example/v1/", "model": "gemma3:27b", "api_key": "model-key",
                "jobs_per_day": 5, "sample_rows": 1000}
        clean = settings.clean_batch(form, {})
        self.assertEqual((clean["KAI_BATCH_MODEL_BASE_URL"], clean["KAI_BATCH_SAMPLE_ROWS"]), ("https://model.example/v1", "1000"))
        self.assertEqual(settings.clean_batch({**form, "api_key": ""}, {"KAI_BATCH_MODEL_API_KEY": "saved"})["KAI_BATCH_MODEL_API_KEY"], "saved")
        for change, message in [({"api_key": ""}, "API key"), ({"base_url": "ftp://x"}, "URL"), ({"model": "a b"}, "model"),
                                ({"sample_rows": 99999}, "sampel")]:
            with self.subTest(change=change), self.assertRaisesRegex(settings.SettingsError, message):
                settings.clean_batch({**form, **change}, {})
        stored = {}

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, sql, params):
                stored["params"] = params

        with mock.patch.object(settings, "_connect", lambda env: Connection()):
            settings.save(clean, "a" * 24, self.env, "batch_model")
        name, config, secret, version, by = stored["params"]
        self.assertEqual(name, "batch_model")
        self.assertNotIn("model-key", config)
        row = (json.loads(config), secret, version, by, datetime(2026, 11, 10, tzinfo=timezone.utc))
        value = settings.effective(self.env, row=row, name="batch_model")
        self.assertEqual(value["KAI_BATCH_MODEL_API_KEY"], "model-key")
        with self.assertRaises(Exception):  # a key encrypted for one section cannot be read as another
            settings.effective(self.env, row=row, name=settings.NAME)
        public = settings.public_batch(value, row)
        self.assertNotIn("model-key", json.dumps(public))
        self.assertTrue(public["secret_set"])

    def test_limits_round_trip_in_friendly_units(self):
        clean = settings.clean_limits({"max_execution_seconds": 20, "max_memory_gb": 8, "max_rows_million": 500,
                                       "max_read_gb": 50, "log_retention_days": 30}, {})
        self.assertEqual(clean["KAI_CH_MAX_MEMORY_USAGE"], str(8 * 1024**3))
        self.assertEqual(settings.public_limits({**clean, "source": "ui"}, None)["settings"]["max_rows_million"], 500)
        self.assertEqual(settings.public_limits({**{k: "" for k in clean}, "source": "env"}, None)["settings"],
                         {"max_execution_seconds": 25, "max_memory_gb": 4, "max_rows_million": 1000, "max_read_gb": 100,
                          "log_retention_days": 90})
        with self.assertRaisesRegex(settings.SettingsError, "waktu"):
            settings.clean_limits({"max_execution_seconds": 60}, {})

    def test_current_prefers_saved_sections_and_survives_a_database_outage(self):
        env = {**self.env, "KAI_CATALOG_DSN": "postgresql://x", "KAI_CH_MAX_EXECUTION_TIME": "25", "KAI_BATCH_MODEL": "env-model"}
        rows = {"limits": ({"KAI_CH_MAX_EXECUTION_TIME": "10"}, None, None, "a" * 24, datetime.now(timezone.utc))}
        settings.reset_current()
        with mock.patch.object(settings, "read_row", lambda e, name: rows.get(name)):
            values = settings.current(env)
        self.assertEqual((values["KAI_CH_MAX_EXECUTION_TIME"], values["KAI_BATCH_MODEL"]), ("10", "env-model"))
        settings.reset_current()

        def down(e, name):
            raise OSError("db down")

        with mock.patch.object(settings, "read_row", down):
            self.assertEqual(settings.current(env)["KAI_CH_MAX_EXECUTION_TIME"], "10")
        settings.reset_current()

    def test_unknown_section_is_404(self):
        with mock.patch.dict(os.environ, {"KAI_PLATFORM_INTERNAL_TOKEN": "internal"}):
            request = Request()
            request.path_params = {"name": "secrets"}
            self.assertEqual(asyncio.run(settings.settings_request(request)).status_code, 404)


if __name__ == "__main__":
    unittest.main()
