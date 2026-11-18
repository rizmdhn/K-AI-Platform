"""Semantic layer table (012): constraints and catalog-role grants, on the disposable PostgreSQL fixture."""
import unittest
from support import LocalPostgresTestCase

ROW = ("INSERT INTO kai.semantic_definitions (kind,name,tables,definition,source) VALUES "
       "('metric','pendapatan_bersih',ARRAY['kai_demo.penjualan'],'{\"expression\":\"sum(pendapatan_rp)\"}','ai')")


class SemanticDefinitionTests(LocalPostgresTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.cleanup)
        # An existing install: the catalog role is there before 012 runs, so the migration grants it.
        self.execute('\\ir /migrations/create_catalog_role.sql\n')
        self.sql('\\ir /migrations/migrate_identity.sql\n\\ir /migrations/migrate_approvals.sql\n'
                 '\\ir /migrations/migrate_enrollment.sql\n\\ir /migrations/migrate_admin_warehouse.sql\n'
                 '\\ir /migrations/migrate_revocation_fence.sql\n\\ir /migrations/migrate.sql\n')

    def cleanup(self):
        self.execute(f'DROP DATABASE {self.database} WITH (FORCE);')
        self.execute('DROP ROLE IF EXISTS kai_catalog_runtime, kai_catalog;')

    def test_defaults_and_constraints(self):
        self.sql(ROW)
        self.assertEqual(self.sql("SELECT status, tables[1] FROM kai.semantic_definitions").stdout.strip(),
                         "draft|kai_demo.penjualan")
        self.sql(ROW.replace("'metric'", "'verified_query'"))  # same name, other kind: allowed
        for statement in [
            ROW,  # duplicate kind + name
            ROW.replace("'metric'", "'dashboard'"),
            ROW.replace("pendapatan_bersih", "Pendapatan Bersih"),
            ROW.replace("ARRAY['kai_demo.penjualan']", "ARRAY[]::text[]"),
            ROW.replace("'{\"expression\":\"sum(pendapatan_rp)\"}'", "'[]'"),
            ROW.replace("'ai')", "'robot')"),
            "UPDATE kai.semantic_definitions SET status='live'",
            "UPDATE kai.semantic_definitions SET proposed_by='not-a-user'",
        ]:
            with self.subTest(statement=statement[-60:]):
                self.assertNotEqual(self.sql(statement, check=False).returncode, 0)

    def test_catalog_role_can_review_but_runtime_cannot_read(self):
        self.sql('\\ir /migrations/grant_catalog_access.sql\n')  # fresh-install path, idempotent with 012's grant
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            with self.subTest(privilege=privilege):
                self.assertEqual(self.sql(f"SELECT has_table_privilege('kai_catalog','kai.semantic_definitions','{privilege}')")
                                 .stdout.strip(), "t")
        self.assertNotEqual(self.sql("SELECT * FROM kai.semantic_definitions", check=False, role="kai_runtime").returncode, 0)
        self.assertEqual(self.sql("SELECT version FROM kai.schema_migrations WHERE version LIKE '012%'").stdout.strip(),
                         "012_semantic_definitions")


if __name__ == "__main__":
    unittest.main()
