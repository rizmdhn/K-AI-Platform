import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ActivationTests(unittest.TestCase):
    def activate(self, directory):
        return subprocess.run(
            [str(ROOT / ".venv/bin/ansible-playbook"), "-i", "localhost,",
             "tests/activation.yml", "--extra-vars", json.dumps({"probe_dir": str(directory)})],
            cwd=ROOT, capture_output=True, text=True,
            env=dict(os.environ, ANSIBLE_HOME=str(ROOT / ".cache/ansible"), ANSIBLE_NOCOLOR="1"),
        )

    def test_retry_activates_already_written_configuration_and_then_converges(self):
        for old_marker in [None, "baseline"]:
            with self.subTest(old_marker=old_marker), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                (directory / "config").write_text("candidate")
                (directory / "loaded").write_text("baseline")
                if old_marker:
                    (directory / "applied").write_text(old_marker)
                result = self.activate(directory)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((directory / "loaded").read_text(), "candidate")
                self.assertEqual((directory / "applied").read_text(), "candidate")
                activated = (directory / "loaded").stat().st_mtime_ns
                result = self.activate(directory)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("changed=0", result.stdout)
                self.assertEqual((directory / "loaded").stat().st_mtime_ns, activated)


if __name__ == "__main__":
    unittest.main()
