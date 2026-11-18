import asyncio
import hashlib
import os
import tempfile
import unittest
from unittest import mock

import httpx2 as httpx

import local_models as models

DATA = b"gguf" * 5000
ENTRY = {"id": "Tiny-Q4", "label": "Tiny", "repo": "org/Tiny-GGUF", "size": len(DATA), "ram_gb": 1, "note": ""}


class LocalModelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.calls, self.sha, self.router = [], hashlib.sha256(DATA).hexdigest(), {"data": []}
        real = httpx.AsyncClient

        def handler(request):
            self.calls.append((request.method, request.url.path, request.headers.get("range")))
            if request.url.path.startswith("/api/models/"):
                return httpx.Response(200, json=[{"path": "Tiny-Q4.gguf", "lfs": {"size": len(DATA), "oid": self.sha}}])
            if "/resolve/" in request.url.path:
                start = int(request.headers["range"].split("=")[1].rstrip("-")) if request.headers.get("range") else 0
                return httpx.Response(206 if start else 200, content=DATA[start:])
            if request.url.path == "/models":
                return httpx.Response(200, json=self.router)
            return httpx.Response(200, json={"success": True})

        def client(*args, **kwargs):
            return real(*args, **{**kwargs, "transport": httpx.MockTransport(handler)})
        for p in (mock.patch.object(models, "ROOT", self.root), mock.patch.object(models, "ROUTER", "http://local-llm:8080"),
                  mock.patch.object(models, "CATALOG", [ENTRY]), mock.patch.object(models.httpx, "AsyncClient", client)):
            p.start()
            self.addCleanup(p.stop)
        models._download.clear()

    async def download(self):
        models.start_download("Tiny-Q4")
        await models._task
        return dict(models._download)

    async def test_download_is_checksum_verified_then_installed(self):
        job = await self.download()
        self.assertEqual((job["status"], job["bytes"]), ("done", len(DATA)))
        self.assertEqual(models.installed(), {"Tiny-Q4": len(DATA)})
        self.assertEqual(models.partial("Tiny-Q4"), 0)

    async def test_partial_download_resumes_with_a_range_request(self):
        os.makedirs(os.path.join(self.root, "downloads"))
        with open(os.path.join(self.root, "downloads", "Tiny-Q4.gguf.part"), "wb") as handle:
            handle.write(DATA[:7000])
        job = await self.download()
        self.assertEqual(job["status"], "done")
        self.assertIn(("GET", "/org/Tiny-GGUF/resolve/main/Tiny-Q4.gguf", "bytes=7000-"), self.calls)

    async def test_checksum_mismatch_discards_the_file(self):
        self.sha = "0" * 64
        job = await self.download()
        self.assertEqual(job["status"], "failed")
        self.assertIn("Checksum", job["message"])
        self.assertEqual((models.installed(), models.partial("Tiny-Q4")), ({}, 0))

    async def test_not_enough_disk_is_refused_before_downloading(self):
        with mock.patch.object(models.shutil, "disk_usage", return_value=mock.Mock(free=10, total=100)):
            job = await self.download()
        self.assertEqual(job["status"], "failed")
        self.assertIn("Ruang disk tidak cukup", job["message"])
        self.assertFalse(any("/resolve/" in path for _, path, _ in self.calls))

    async def test_only_catalog_models_and_safe_names(self):
        for bad in ("../etc/passwd", "a/b", "Other-Model", "", None):
            with self.subTest(bad=bad), self.assertRaises(models.ModelError):
                models.start_download(bad)
        with self.assertRaises(models.ModelError):
            await models.delete("../../active")

    async def test_activate_delete_and_manual_files(self):
        gguf = os.path.join(self.root, "gguf")
        os.makedirs(gguf)
        for name in ("Tiny-Q4.gguf", "Manual-7B.gguf"):
            open(os.path.join(gguf, name), "wb").write(b"x")
        self.router = {"data": [{"id": "Tiny-Q4", "status": {"value": "unloaded"}}]}
        self.assertIn("dimuat", await models.activate("Tiny-Q4"))
        self.assertEqual(models.active(), "Tiny-Q4")
        self.assertIn(("POST", "/models/load", None), self.calls)
        state = await models.state()
        self.assertEqual({m["id"]: m["source"] for m in state["models"]}, {"Tiny-Q4": "catalog", "Manual-7B": "manual"})
        with self.assertRaises(models.ModelError):
            await models.delete("Tiny-Q4", job_running=True)
        await models.delete("Tiny-Q4")
        self.assertIn(("POST", "/models/unload", None), self.calls)
        self.assertEqual((models.active(), list(models.installed())), (None, ["Manual-7B"]))

    async def test_activate_reports_a_stopped_model_server(self):
        os.makedirs(os.path.join(self.root, "gguf"))
        open(os.path.join(self.root, "gguf", "Tiny-Q4.gguf"), "wb").write(b"x")
        with mock.patch.object(models, "router_models", mock.AsyncMock(return_value=None)):
            with self.assertRaises(models.ModelError) as caught:
                await models.activate("Tiny-Q4")
        self.assertIn("make llm-up", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
