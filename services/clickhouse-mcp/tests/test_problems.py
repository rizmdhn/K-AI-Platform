"""P6-10: the admin problem summary is token-gated, sanitized, and windowed to 24 hours."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import httpx2 as httpx
from starlette.applications import Starlette
from starlette.routing import Route

import problems

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
A, B = "a" * 24, "b" * 24


def line(ts, user, outcome, **extra):
    return json.dumps({"ts": ts, "audit": {"librechat_user": user, "outcome": outcome, "ch_user": "alice_ch",
                                           "sql_sha256": "f" * 64, **extra}})


LINES = [
    line("2026-11-08T11:00:00Z", A, "login_rejected"),       # older than 24 h: ignored
    line("2026-11-09T09:00:00Z", A, "ok"),
    line("2026-11-09T10:00:00Z", A, "login_rejected"),
    line("2026-11-09T10:05:00Z", B, "access_denied"),
    line("2026-11-09T10:06:00Z", B, "unsafe_sql"),
    line("2026-11-09T10:07:00Z", B, "made_up_secret_value"),  # unknown outcome is not echoed
    line("2026-11-09T10:08:00Z", B, "cancelled"),
    line("2026-11-09T10:09:00Z", "not-a-user", "ok"),
    "garbage {",
]


class SummaryTests(unittest.TestCase):
    def test_summary_counts_last_outcomes_in_window(self):
        result = problems.summarize(LINES, NOW)
        self.assertEqual((result["calls_24h"], result["errors_24h"]), (5, 4))
        self.assertEqual(result["users"][A], {"last_ok": "2026-11-09T09:00:00Z", "errors": 1, "denied": 0,
                                              "last_error": {"outcome": "login_rejected", "at": "2026-11-09T10:00:00Z"}})
        self.assertEqual(result["users"][B]["denied"], 2)
        self.assertEqual(result["users"][B]["last_error"]["outcome"], "error")
        text = json.dumps(result)
        for private in ("alice_ch", "f" * 64, "made_up_secret_value", "not-a-user"):
            self.assertNotIn(private, text)

    def test_reads_current_and_rotated_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "audit.jsonl.2026-11-08"), "w") as f:
                f.write(LINES[1] + "\n")
            with open(os.path.join(directory, "audit.jsonl"), "w") as f:
                f.write(LINES[2] + "\n")
            os.utime(os.path.join(directory, "audit.jsonl.2026-11-08"), (1, 1))
            result = problems.read_audit(directory, NOW)
        self.assertEqual(result["users"][A]["last_ok"], "2026-11-09T09:00:00Z")
        self.assertEqual(result["users"][A]["last_error"]["outcome"], "login_rejected")


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def get(self, headers, env):
        app = Starlette(routes=[Route("/p", problems.problems_request)])
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(problems, "check", return_value={"KAI_BINDING_DSN": True, "keyring": False}), \
             mock.patch.object(problems, "_active_subjects", mock.AsyncMock(return_value=[A])):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                return await client.get("/p", headers=headers)

    async def test_requires_internal_token(self):
        env = {"KAI_PLATFORM_INTERNAL_TOKEN": "internal-secret"}
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "internal-secret"}):
            self.assertEqual((await self.get(headers, env)).status_code, 401)
        self.assertEqual((await self.get({"Authorization": "Bearer x"}, {"KAI_PLATFORM_INTERNAL_TOKEN": ""})).status_code, 401)

    async def test_returns_sanitized_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "audit.jsonl"), "w") as f:
                f.write("\n".join(LINES[1:4]) + "\n")
            env = {"KAI_PLATFORM_INTERNAL_TOKEN": "internal-secret", "KAI_AUDIT_LOG_DIR": directory,
                   "KAI_BINDING_DSN": "host=private.invalid password=private_password"}
            with mock.patch.object(problems, "datetime", wraps=datetime) as clock:
                clock.now.return_value = NOW
                response = await self.get({"Authorization": "Bearer internal-secret"}, env)
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["audit_available"] and body["database_available"])
        self.assertEqual(body["active_subjects"], [A])
        self.assertEqual(body["checks"], {"binding_dsn": True, "keyring": False, "warehouse_target": False})
        self.assertEqual(body["users"][A]["last_error"]["outcome"], "login_rejected")
        self.assertNotIn("private_password", response.text)

    async def test_missing_audit_and_database_degrade_to_flags(self):
        env = {"KAI_PLATFORM_INTERNAL_TOKEN": "internal-secret", "KAI_AUDIT_LOG_DIR": "/nonexistent-kai"}
        with mock.patch.object(problems, "_active_subjects", mock.AsyncMock(side_effect=OSError("dsn secret"))):
            app = Starlette(routes=[Route("/p", problems.problems_request)])
            with mock.patch.dict(os.environ, env), mock.patch.object(problems, "check", side_effect=RuntimeError()):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                    response = await client.get("/p", headers={"Authorization": "Bearer internal-secret"})
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(body["audit_available"] or body["database_available"])
        self.assertNotIn("dsn secret", response.text)


if __name__ == "__main__":
    unittest.main()
