"""Audit table (013): constraints, catalog-role grants, and the indexes the Admin Panel log relies on."""
import unittest
from support import LocalPostgresTestCase

ROW = ("INSERT INTO kai.audit_events (ts,librechat_user,tool,ch_user,outcome,rows,duration_ms) VALUES "
       "(now(),'" + "a" * 24 + "','execute_readonly_sql','alice_ch','ok',3,120)")


class AuditEventTests(LocalPostgresTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.cleanup)
        self.execute('\\ir /migrations/create_catalog_role.sql\n')
        self.sql('\\ir /migrations/migrate_identity.sql\n\\ir /migrations/migrate_approvals.sql\n'
                 '\\ir /migrations/migrate_enrollment.sql\n\\ir /migrations/migrate_admin_warehouse.sql\n'
                 '\\ir /migrations/migrate_revocation_fence.sql\n\\ir /migrations/migrate.sql\n')

    def cleanup(self):
        self.execute(f'DROP DATABASE {self.database} WITH (FORCE);')
        self.execute('DROP ROLE IF EXISTS kai_catalog_runtime, kai_catalog;')

    def test_constraints(self):
        self.sql(ROW)
        for statement in [
            ROW.replace("'" + "a" * 24 + "'", "'not-a-user'"),
            ROW.replace("'ok'", "NULL"),
            ROW.replace("'execute_readonly_sql'", "'" + "x" * 65 + "'"),
        ]:
            with self.subTest(statement=statement[-60:]):
                self.assertNotEqual(self.sql(statement, check=False).returncode, 0)

    def test_catalog_role_grants_and_indexes(self):
        self.sql('\\ir /migrations/grant_catalog_access.sql\n')
        for privilege, expected in (("SELECT", "t"), ("INSERT", "t"), ("DELETE", "t"), ("UPDATE", "f")):
            with self.subTest(privilege=privilege):  # audit rows are never edited, only aged out
                self.assertEqual(self.sql(f"SELECT has_table_privilege('kai_catalog','kai.audit_events','{privilege}')")
                                 .stdout.strip(), expected)
        indexes = self.sql("SELECT string_agg(indexname, ',' ORDER BY indexname) FROM pg_indexes WHERE tablename='audit_events'").stdout.strip()
        self.assertEqual(indexes, "audit_events_outcome_idx,audit_events_pkey,audit_events_ts_idx,audit_events_user_idx")


if __name__ == "__main__":
    unittest.main()
