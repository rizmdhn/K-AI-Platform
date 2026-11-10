import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "package_release", ROOT / "scripts/package_release.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def source(self, root):
        for name in [
            "Makefile",
            ".env.example",
            ".env",
            "deploy/librechat.yaml",
            "deploy/docker-compose.override.yml",
            "deploy/upstream/docker-compose.yml",
            "deploy/branding/Dockerfile",
            "scripts/bootstrap.py",
            "scripts/configure_managed_chat.cjs",
            "services/clickhouse-mcp/Dockerfile",
            "db/migrate.sql",
            "services/platform-api/accounts.cjs",
            "services/platform-api/Dockerfile",
            "services/admin-panel/Dockerfile",
            "services/admin-panel/panel/src/server/kaiAccounts.ts",
            "services/admin-panel/panel/src/components/users/CreateUserDialog.tsx",
            "services/admin-panel/panel/patches/react-movable+3.4.1.patch",
            "services/admin-panel/panel/.env",
            "services/admin-panel/panel/node_modules/x/index.js",
            "services/admin-panel/panel/.github/workflows/ci.yml",
            "services/admin-panel/private/.env",
            "deploy/runtime/.env",
            "deploy/runtime/metrics.env",
            "deploy/ansible/inventories/prod/hosts.yml",
            ".git/config",
            "services/clickhouse-mcp/.venv/private.py",
        ]:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("private" if "runtime" in name else name)

    def test_release_contains_every_file_docker_builds_copy_and_compose_mounts(self):
        """The servers build from the package alone; a file only on the laptop breaks the build there."""
        import re
        needed = set()
        for dockerfile in ROOT.glob("**/Dockerfile"):
            # panel/Dockerfile is the upstream Admin Panel's own; ours is services/admin-panel/Dockerfile.
            if any(part in {"node_modules", ".venv", "runtime", "panel"} for part in dockerfile.parts):
                continue
            for line in dockerfile.read_text().splitlines():
                match = re.match(r"\s*COPY\s+(?!--from)(?:--\S+\s+)*(.+)\s+\S+\s*$", line)
                for source in (match[1].split() if match else []):
                    for path in dockerfile.parent.glob(source.rstrip("/")):
                        files = path.rglob("*") if path.is_dir() else [path]
                        needed |= {f.relative_to(ROOT).as_posix() for f in files if f.is_file() and f.name != ".DS_Store"}
        needed = {n for n in needed if not n.startswith(release.PANEL) or release.panel_file(n)}  # .dockerignore
        for compose in (ROOT / "deploy").glob("*.yml"):
            for source in re.findall(r"^\s*-\s*(\.\.?/[^:${}]+):", compose.read_text(), re.M):
                path = (compose.parent / source).resolve()
                if path.is_file():
                    needed.add(path.relative_to(ROOT).as_posix())
        self.assertIn("services/admin-panel/panel/src/login-branding.css", needed)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "release.tgz"
            release.package(ROOT, output)
            with tarfile.open(output) as archive:
                missing = needed - set(archive.getnames())
        self.assertEqual(missing, set(), "add these to PATTERNS in scripts/package_release.py")

    def test_release_is_deterministic_and_excludes_private_runtime_and_controller_material(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            self.source(source)
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            digest = release.package(source, first)
            self.assertEqual(digest, release.package(source, second))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)
            self.assertEqual(release.verify(first), digest)
            with tarfile.open(first) as archive:
                names = archive.getnames()
                self.assertIn("Makefile", names)
                self.assertIn("db/migrate.sql", names)
                self.assertIn("services/platform-api/accounts.cjs", names)
                self.assertIn("services/admin-panel/panel/src/server/kaiAccounts.ts", names)
                self.assertIn("services/admin-panel/panel/patches/react-movable+3.4.1.patch", names)
                self.assertNotIn("services/admin-panel/panel/.env", names)
                self.assertNotIn("services/admin-panel/panel/node_modules/x/index.js", names)
                self.assertNotIn("services/admin-panel/panel/.github/workflows/ci.yml", names)
                self.assertNotIn("services/admin-panel/private/.env", names)
                self.assertNotIn(".env", names)
                self.assertIn(".env.example", names)
                self.assertFalse(
                    any(
                        "runtime" in n
                        or "inventories" in n
                        or ".git/" in n
                        or ".venv/" in n
                        for n in names
                    )
                )
            with self.assertRaises(FileExistsError):
                release.package(source, first)

    def test_source_symlinks_and_tampered_duplicate_or_traversing_archives_are_refused(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            self.source(source)
            p = source / "scripts/unsafe.py"
            p.symlink_to(source / "deploy/runtime/.env")
            with self.assertRaises(ValueError):
                release.package(source, root / "unsafe.tar.gz")
            p.unlink()
            os.link(source / "deploy/runtime/.env", p)
            with self.assertRaises(ValueError):
                release.package(source, root / "hardlink.tar.gz")
            p.unlink()
            valid = root / "valid.tar.gz"
            release.package(source, valid)
            with tarfile.open(valid) as baseline:
                files = {
                    entry.name: baseline.extractfile(entry).read() for entry in baseline
                }
            for case in ["traversal", "duplicate", "tamper", "canonical_alias"]:
                entries = list(files.items())
                if case == "traversal":
                    manifest = json.loads(files["release.json"])
                    manifest["files"]["../escape.py"] = hashlib.sha256(
                        b"bad"
                    ).hexdigest()
                    entries = [
                        (
                            name,
                            (
                                json.dumps(manifest).encode()
                                if name == "release.json"
                                else data
                            ),
                        )
                        for name, data in entries
                    ]
                    entries.append(("../escape.py", b"bad"))
                elif case == "canonical_alias":
                    alias = "deploy//docker-compose.override.yml"
                    manifest = json.loads(files["release.json"])
                    manifest["files"][alias] = hashlib.sha256(
                        b"alias-overwrite"
                    ).hexdigest()
                    entries = [
                        (
                            name,
                            (
                                json.dumps(manifest).encode()
                                if name == "release.json"
                                else data
                            ),
                        )
                        for name, data in entries
                    ]
                    entries.append((alias, b"alias-overwrite"))
                elif case == "duplicate":
                    entries.append(("Makefile", files["Makefile"]))
                else:
                    entries = [
                        (name, b"tampered" if name == "Makefile" else data)
                        for name, data in entries
                    ]
                archive = root / "bad.tar.gz"
                with tarfile.open(archive, "w:gz") as output:
                    for name, data in entries:
                        entry = tarfile.TarInfo(name)
                        entry.size = len(data)
                        entry.mode = 0o644
                        output.addfile(entry, io.BytesIO(data))
                with self.assertRaises(ValueError):
                    release.verify(archive)
