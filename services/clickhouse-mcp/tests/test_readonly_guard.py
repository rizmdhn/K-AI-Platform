import unittest
from types import SimpleNamespace as S
from unittest import mock

import per_user_middleware as mw
from tool_errors import GatewayError


class ReadonlyGuardTests(unittest.TestCase):
    def guard(self, settings):
        with mock.patch.object(mw, "_upstream_query_settings", return_value={"ok": 1}):
            return mw._readonly_query_settings(S(server_settings=settings))

    def test_changeable_or_locked_readonly_passes(self):
        self.assertEqual(self.guard({"readonly": S(value="0", readonly=False)}), {"ok": 1})
        self.assertEqual(self.guard({"readonly": S(value="2", readonly=True)}), {"ok": 1})

    def test_readonly_locked_at_zero_or_unknown_is_refused(self):
        for settings in ({"readonly": S(value="0", readonly=True)}, {}, None):
            with self.subTest(settings=settings), self.assertRaises(GatewayError):
                self.guard(settings)


if __name__ == "__main__":
    unittest.main()
