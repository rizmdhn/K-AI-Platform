import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ValidationPlaybookTests(unittest.TestCase):
    def run_validation(self, **changes):
        values = {
            "ansible_facts": {
                "distribution": "Ubuntu", "distribution_version": "24.04",
                "architecture": "aarch64", "service_mgr": "systemd",
            },
            "kai_admin_cidrs": ["192.0.2.7/32"],
            "ansible_port": 22,
        }
        values.update(changes)
        return subprocess.run(
            [str(ROOT / ".venv/bin/ansible-playbook"), "-i", "localhost,",
             "tests/validate.yml", "--extra-vars", json.dumps(values)],
            cwd=ROOT, capture_output=True, text=True,
            env=dict(os.environ, ANSIBLE_HOME=str(ROOT / ".cache/ansible"), ANSIBLE_NOCOLOR="1"),
        )

    def test_valid_inventory_passes_without_host_changes(self):
        result = self.run_validation()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("changed=0", result.stdout)

    def test_missing_allowlist_fails_before_host_changes(self):
        result = self.run_validation(kai_admin_cidrs=[])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-empty list", result.stdout)

    def test_unsupported_os_fails_before_host_changes(self):
        result = self.run_validation(ansible_facts={
            "distribution": "Debian", "distribution_version": "12",
            "architecture": "x86_64", "service_mgr": "systemd",
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires Ubuntu 24.04", result.stdout)

    def test_invalid_ssh_port_fails_before_host_changes(self):
        result = self.run_validation(ansible_port=0)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("valid SSH port", result.stdout)

    def test_ssh_cannot_share_the_public_https_port(self):
        result = self.run_validation(ansible_port=443)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("443", result.stdout)


if __name__ == "__main__":
    unittest.main()
