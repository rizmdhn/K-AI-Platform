import importlib.util
from pathlib import Path
import unittest


PLUGIN = Path(__file__).resolve().parents[1] / "roles/kai_host/filter_plugins/admin_networks.py"


class AdminNetworksTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("admin_networks", PLUGIN)
        self.plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.plugin)

    def test_accepts_explicit_ipv4_and_ipv6_networks_and_removes_duplicates(self):
        self.assertEqual(
            self.plugin.admin_networks(["192.0.2.7/32", "2001:db8::/48", "192.0.2.7/32"]),
            ["192.0.2.7/32", "2001:db8::/48"],
        )

    def test_rejects_missing_malformed_and_world_open_allowlists(self):
        for value in [None, [], "192.0.2.7/32", [True], [""], ["any"],
                      ["192.0.2.7"], ["0.0.0.0/0"], ["::/0"],
                      ["192.0.2.7/24"], ["192.0.2.999/32"], ["2001:db8::/129"]]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.plugin.admin_networks(value)

    def test_current_ssh_source_must_remain_allowed(self):
        self.assertEqual(
            self.plugin.admin_networks(["192.0.2.0/24"], "192.0.2.7"), ["192.0.2.0/24"]
        )
        with self.assertRaises(ValueError):
            self.plugin.admin_networks(["192.0.2.0/24"], "198.51.100.7")
        with self.assertRaises(ValueError):
            self.plugin.admin_networks(["2001:db8::/48"], "192.0.2.7")


if __name__ == "__main__":
    unittest.main()
