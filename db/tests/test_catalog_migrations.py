"""Run explicitly: python3 -m unittest discover -s db/tests -v.

Uses only a unique, disposable local Docker database. No published ports or volumes.
"""

import unittest
from concurrent.futures import ThreadPoolExecutor
from support import LocalPostgresTestCase


class CatalogMigrationTests(LocalPostgresTestCase):
    def prerequisite(self):
        self.sql("CREATE SCHEMA kai AUTHORIZATION kai_migrator;"
                 "CREATE TABLE kai.ai_principals (principal_id uuid PRIMARY KEY);")

    def migrate(self, check=True, role="kai_migrator"):
        return self.sql("\\ir /migrations/migrate.sql\n", check, role)

    def install(self):
        self.prerequisite()
        self.migrate()

    def test_missing_principal_table_rolls_back_everything(self):
        result = self.migrate(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kai.ai_principals", result.stderr)
        self.assertEqual(self.sql("SELECT count(*) FROM pg_namespace WHERE nspname='kai'").stdout.strip(), "0")

    def test_repeat_and_concurrent_application_preserve_data(self):
        self.prerequisite()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.migrate(), range(2)))
        self.sql("INSERT INTO kai.catalog_assets (asset_id,source_type,source_ref,title)"
                 "VALUES ('00000000-0000-0000-0000-000000000001','clickhouse','db.table','Original');")
        self.migrate()
        result = self.sql("SELECT title FROM kai.catalog_assets;"
                          "SELECT count(*) FROM kai.schema_migrations;"
                          "SELECT value FROM public.rag_sentinel;")
        self.assertEqual(result.stdout.splitlines(), ["Original", "1", "upstream remains intact"])

    def test_asset_defaults_uniqueness_and_metadata_validation(self):
        self.install()
        insert = ("INSERT INTO kai.catalog_assets (asset_id,source_type,source_ref,title) VALUES "
                  "('00000000-0000-0000-0000-000000000001','clickhouse','db.table','Table');")
        self.sql(insert)
        self.assertEqual(self.sql("SELECT approval_status,verified_metadata,proposed_metadata "
                                  "FROM kai.catalog_assets").stdout.strip(), "pending|{}|{}")
        for statement in [
            insert.replace("000000000001", "000000000002"),
            "UPDATE kai.catalog_assets SET source_type='unknown'",
            "UPDATE kai.catalog_assets SET source_ref='  '",
            "UPDATE kai.catalog_assets SET source_ref=E'\\t\\n'",
            "UPDATE kai.catalog_assets SET verified_metadata='[]'",
            "UPDATE kai.catalog_assets SET proposed_metadata='null'",
        ]:
            with self.subTest(statement=statement):
                self.assertNotEqual(self.sql(statement, check=False).returncode, 0)

    def test_reviews_require_real_asset_and_principal(self):
        self.install()
        self.sql("INSERT INTO kai.ai_principals VALUES ('00000000-0000-0000-0000-000000000001');"
                 "INSERT INTO kai.catalog_assets (asset_id,source_type,source_ref,title) VALUES "
                 "('00000000-0000-0000-0000-000000000002','document','file-1','Document');")
        insert = ("INSERT INTO kai.metadata_reviews (review_id,asset_id,reviewer_principal,decision,approved_payload) "
                  "VALUES ('00000000-0000-0000-0000-000000000003',"
                  "'00000000-0000-0000-0000-000000000002',"
                  "'00000000-0000-0000-0000-000000000001','approved','{}');")
        for statement in [
            insert.replace("000000000002", "000000000009"),
            insert.replace("000000000001", "000000000009"),
            insert.replace("'approved'", "'invented'"),
            insert.replace("'{}'", "'[]'"),
        ]:
            with self.subTest(statement=statement):
                self.assertNotEqual(self.sql(statement, check=False).returncode, 0)
        self.sql(insert)
        self.assertNotEqual(self.sql("DELETE FROM kai.catalog_assets", check=False).returncode, 0)
        self.assertNotEqual(self.sql("DELETE FROM kai.ai_principals", check=False).returncode, 0)

    def test_job_idempotency_status_and_attempts(self):
        self.install()
        insert = ("INSERT INTO kai.ingestion_jobs (job_id,source_type,source_ref,source_version,status) VALUES "
                  "('00000000-0000-0000-0000-000000000001','clickhouse','db.table','v1','queued');")
        self.sql(insert)
        self.assertEqual(self.sql("SELECT attempts FROM kai.ingestion_jobs").stdout.strip(), "0")
        for statement in [
            insert.replace("000000000001", "000000000002"),
            "UPDATE kai.ingestion_jobs SET status='unknown'",
            "UPDATE kai.ingestion_jobs SET attempts=-1",
            "UPDATE kai.ingestion_jobs SET source_type='unknown'",
            "UPDATE kai.ingestion_jobs SET source_version=''",
            "UPDATE kai.ingestion_jobs SET source_ref=E'\\t\\n'",
            "UPDATE kai.ingestion_jobs SET source_version=E'\\t\\n'",
        ]:
            with self.subTest(statement=statement):
                self.assertNotEqual(self.sql(statement, check=False).returncode, 0)
        self.sql(insert.replace("000000000001", "000000000002").replace("'v1'", "'v2'"))
        self.assertEqual(self.sql("SELECT count(*) FROM kai.ingestion_jobs").stdout.strip(), "2")

    def test_runtime_has_no_implicit_data_or_ddl_access(self):
        self.install()
        for statement in ["SELECT * FROM kai.catalog_assets", "CREATE TABLE kai.injected (id int)",
                          "DROP TABLE kai.ingestion_jobs", "SELECT * FROM kai.schema_migrations"]:
            with self.subTest(statement=statement):
                self.assertNotEqual(self.sql(statement, check=False, role="kai_runtime").returncode, 0)
        self.assertNotEqual(self.migrate(check=False, role="kai_runtime").returncode, 0)
        self.assertEqual(self.sql("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                  "WHERE n.nspname='kai' AND c.relowner <> 'kai_migrator'::regrole").stdout.strip(), "0")

    def test_supporting_indexes_exist(self):
        self.install()
        indexes = self.sql("SELECT indexname FROM pg_indexes WHERE schemaname='kai'").stdout.splitlines()
        self.assertTrue({"metadata_reviews_asset_created_idx", "ingestion_jobs_available_idx",
                         "ingestion_jobs_lease_idx"}.issubset(indexes))

    def test_ddl_failure_leaves_no_partial_catalog(self):
        self.prerequisite()
        self.sql("CREATE TABLE kai.metadata_reviews (sentinel text);"
                 "INSERT INTO kai.metadata_reviews VALUES ('existing table');")
        self.assertNotEqual(self.migrate(check=False).returncode, 0)
        self.assertEqual(self.sql("SELECT to_regclass('kai.catalog_assets') IS NULL;"
                                  "SELECT to_regclass('kai.schema_migrations') IS NULL;"
                                  "SELECT sentinel FROM kai.metadata_reviews").stdout.splitlines(),
                         ["t", "t", "existing table"])


if __name__ == "__main__":
    unittest.main()
