"""P6-10: the admin problem summary and access log routes are token-gated, validated and degrade to flags."""
import os
import unittest
from unittest import mock

import httpx2 as httpx
from starlette.applications import Starlette
from starlette.routing import Route

import problems

A, B = "a" * 24, "b" * 24


SUMMARY = {"calls_24h": 5, "errors_24h": 4, "users": {A: {"last_ok": "2026-11-09T09:00:00Z", "errors": 1, "denied": 0,
          "blocked": 0, "last_error": {"outcome": "login_rejected", "at": "2026-11-09T10:00:00Z"}}}}


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def get(self, headers, env):
        app = Starlette(routes=[Route("/p", problems.problems_request)])
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(problems, "check", return_value={"KAI_BINDING_DSN": True, "keyring": False}), \
             mock.patch.object(problems, "_active_subjects", mock.AsyncMock(return_value={A: "2026-11-09T10:00:00Z"})):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                return await client.get("/p", headers=headers)

    async def test_requires_internal_token(self):
        env = {"KAI_PLATFORM_INTERNAL_TOKEN": "internal-secret"}
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "internal-secret"}):
            self.assertEqual((await self.get(headers, env)).status_code, 401)
        self.assertEqual((await self.get({"Authorization": "Bearer x"}, {"KAI_PLATFORM_INTERNAL_TOKEN": ""})).status_code, 401)

    async def test_returns_sanitized_summary(self):
        env = {"KAI_PLATFORM_INTERNAL_TOKEN": "internal-secret", "KAI_CATALOG_DSN": "host=private.invalid",
               "KAI_BINDING_DSN": "host=private.invalid password=private_password"}
        with mock.patch.object(problems.audit_store, "summary", return_value=SUMMARY) as summary:
            response = await self.get({"Authorization": "Bearer internal-secret"}, env)
        self.assertEqual(summary.call_args.args[0], "host=private.invalid")
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["audit_available"] and body["database_available"])
        self.assertEqual(body["active_subjects"], [A])
        self.assertEqual(body["credentials_verified_at"], {A: "2026-11-09T10:00:00Z"})
        self.assertEqual(body["checks"], {"binding_dsn": True, "keyring": False, "warehouse_target": False})
        self.assertEqual(body["users"][A]["last_error"]["outcome"], "login_rejected")
        self.assertNotIn("private_password", response.text)

    async def test_missing_audit_and_database_degrade_to_flags(self):
        env = {"KAI_PLATFORM_INTERNAL_TOKEN": "internal-secret", "KAI_CATALOG_DSN": "host=private.invalid"}
        with mock.patch.object(problems, "_active_subjects", mock.AsyncMock(side_effect=OSError("dsn secret"))), \
             mock.patch.object(problems.audit_store, "summary", side_effect=OSError("catalog secret")):
            app = Starlette(routes=[Route("/p", problems.problems_request)])
            with mock.patch.dict(os.environ, env), mock.patch.object(problems, "check", side_effect=RuntimeError()):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                    response = await client.get("/p", headers={"Authorization": "Bearer internal-secret"})
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(body["audit_available"] or body["database_available"])
        self.assertNotIn("dsn secret", response.text)
        self.assertNotIn("catalog secret", response.text)


class LogRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_validates_passes_filters_and_degrades(self):
        app = Starlette(routes=[Route("/a", problems.log_request)])
        env = {"KAI_PLATFORM_INTERNAL_TOKEN": "internal-secret", "KAI_CATALOG_DSN": "dsn"}
        auth = {"Authorization": "Bearer internal-secret"}
        page = {"rows": [], "counts": {"access_denied": 1}, "total": 1, "users": [B], "tools": [], "next_cursor": None}
        with mock.patch.dict(os.environ, env), mock.patch.object(problems.audit_store, "read_log", return_value=page) as read:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                self.assertEqual((await client.get("/a")).status_code, 401)
                for bad in ("days=2", "user=x", "outcome=drop", "tool=A;B", "cursor=" + "x" * 33):
                    self.assertEqual((await client.get(f"/a?{bad}", headers=auth)).status_code, 400)
                body = (await client.get(f"/a?days=90&user={B}&outcome=access_denied&cursor=MTA", headers=auth)).json()
                self.assertEqual(read.call_args.args[2:], (90, B, "access_denied", "", "MTA"))
                read.side_effect = OSError("down")
                self.assertFalse((await client.get("/a", headers=auth)).json()["available"])
        self.assertTrue(body["available"])
        self.assertEqual(body["total"], 1)


if __name__ == "__main__":
    unittest.main()
