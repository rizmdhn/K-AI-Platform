import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "configure_metrics", ROOT / "scripts/configure_metrics.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class MetricSetupTests(unittest.TestCase):
    def test_creates_once_private_distinct_token_and_preserves_existing_material(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            self.assertTrue(setup.configure(runtime))
            path = runtime / ".env"
            data = path.read_bytes()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
            value = dict(
                line.split("=", 1)
                for line in data.decode().splitlines()
                if line and not line.startswith("#")
            )["KAI_METRICS_TOKEN"]
            self.assertEqual(len(value), 64)
            self.assertFalse(setup.configure(runtime))
            self.assertEqual(path.read_bytes(), data)
            other = Path(directory) / "other"
            setup.configure(other)
            self.assertNotEqual(data, (other / ".env").read_bytes())

    def test_refuses_symlink_target_and_insecure_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir(mode=0o700)
            target = root / "unrelated"
            target.write_text("keep")
            (runtime / ".env").symlink_to(target)
            with self.assertRaises(ValueError):
                setup.configure(runtime)
            self.assertEqual(target.read_text(), "keep")
            (runtime / ".env").unlink()
            (runtime / ".env").write_text("KAI_METRICS_TOKEN=" + "a" * 64 + "\n")
            (runtime / ".env").chmod(0o644)
            with self.assertRaises(ValueError):
                setup.configure(runtime)
