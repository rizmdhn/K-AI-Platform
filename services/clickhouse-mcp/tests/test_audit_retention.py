import json
import logging
import os
import tempfile
import unittest
from unittest import mock

import per_user_middleware as mw


class AuditRetentionTests(unittest.TestCase):
    def test_audit_file_keeps_configured_days_as_json_lines(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {"KAI_LOG_RETENTION_DAYS": "90"}):
            handler = mw.attach_audit_file(directory)
            try:
                self.assertEqual((handler.when, handler.utc, handler.backupCount), ("MIDNIGHT", True, 90))
                mw.audit.info(json.dumps({"request_id": "r1", "outcome": "ok"}))
                handler.flush()
                with open(os.path.join(directory, "audit.jsonl")) as f:
                    line = json.loads(f.readline())
                self.assertEqual(line["audit"]["request_id"], "r1")
                self.assertRegex(line["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            finally:
                mw.audit.removeHandler(handler)
                handler.close()

    def test_invalid_retention_is_refused(self):
        for raw in ("0", "abc", "-5", "99999"):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"KAI_LOG_RETENTION_DAYS": raw}):
                with self.assertRaises(RuntimeError):
                    mw._retention_days()


if __name__ == "__main__":
    unittest.main()
