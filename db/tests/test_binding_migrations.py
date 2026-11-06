"""Historical migration 000 behavior in isolation; migration 004 removes approval gates."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from support import LocalPostgresTestCase

TARGET = "00000000-0000-0000-0000-000000000001"
OWNER = "00000000-0000-0000-0000-000000000002"
DATA_OWNER = "00000000-0000-0000-0000-000000000003"
BINDING = "00000000-0000-0000-0000-000000000004"


class BindingMigrationTests(LocalPostgresTestCase):
    def migrate(self, check=True, role="kai_migrator"):
        return self.sql("\\ir /migrations/migrate_identity.sql\n", check, role)

    def seed(self):
        self.migrate()
        self.sql(f"""
            INSERT INTO kai.ai_principals (principal_id,issuer,subject,librechat_user_id) VALUES
              ('{TARGET}','librechat-local','user-1','user-1'),
              ('{OWNER}','librechat-local','owner-1','owner-1'),
              ('{DATA_OWNER}','librechat-local','data-owner-1','data-owner-1');
            INSERT INTO kai.approved_ch_accounts (principal_id,ch_username) VALUES ('{TARGET}','personal_alice');
            INSERT INTO kai.ch_bindings (binding_id,principal_id,ch_username,cipher_blob,key_version,status,verified_at)
              VALUES ('{BINDING}','{TARGET}','personal_alice',decode(repeat('00',29),'hex'),1,'active',now());
        """)

    def approve(self):
        self.sql(f"""
            INSERT INTO kai.warehouse_approval_authorities (principal_id,approval_role) VALUES
              ('{OWNER}','platform_owner'), ('{DATA_OWNER}','data_owner');
            UPDATE kai.approved_ch_accounts SET owner_approved_by='{OWNER}', owner_approved_at=now(),
              data_owner_approved_by='{DATA_OWNER}', data_owner_approved_at=now();
        """)

    def visible(self, expected, role="kai_migrator"):
        self.assertEqual(self.sql("SELECT count(*) FROM kai.active_ch_bindings", role=role).stdout.strip(), str(expected))

    def test_repeat_concurrent_install_and_catalog_compatibility(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.migrate(), range(2)))
        self.sql(f"INSERT INTO kai.ai_principals (principal_id,issuer,subject) VALUES ('{TARGET}','librechat-local','alice');")
        self.migrate()
        self.sql("\\ir /migrations/migrate.sql\n")
        self.assertEqual(self.sql("SELECT count(*) FROM kai.ai_principals; SELECT count(*) FROM kai.schema_migrations;"
                                  "SELECT value FROM public.rag_sentinel;").stdout.splitlines(),
                         ['1', '2', 'upstream remains intact'])

    def test_partial_collision_rolls_back_new_objects_and_ledger(self):
        self.sql("CREATE SCHEMA kai AUTHORIZATION kai_migrator; CREATE TABLE kai.approved_ch_accounts (sentinel text);"
                 "INSERT INTO kai.approved_ch_accounts VALUES ('keep');")
        self.assertNotEqual(self.migrate(check=False).returncode, 0)
        self.assertEqual(self.sql("SELECT to_regclass('kai.ai_principals') IS NULL;"
                                  "SELECT to_regclass('kai.schema_migrations') IS NULL;"
                                  "SELECT sentinel FROM kai.approved_ch_accounts").stdout.splitlines(), ['t', 't', 'keep'])

    def test_two_approvals_and_designated_authorities_are_all_required(self):
        self.seed()
        self.visible(0)
        self.sql(f"UPDATE kai.approved_ch_accounts SET owner_approved_by='{OWNER}', owner_approved_at=now();")
        self.visible(0)
        self.sql(f"UPDATE kai.approved_ch_accounts SET data_owner_approved_by='{DATA_OWNER}', data_owner_approved_at=now();")
        self.visible(0)
        self.sql(f"INSERT INTO kai.warehouse_approval_authorities VALUES ('{OWNER}','platform_owner',true,now());")
        self.visible(0)
        self.sql(f"INSERT INTO kai.warehouse_approval_authorities VALUES ('{DATA_OWNER}','platform_owner',true,now());")
        self.visible(0)
        self.sql(f"INSERT INTO kai.warehouse_approval_authorities VALUES ('{DATA_OWNER}','data_owner',true,now());")
        self.visible(1)

    def test_inactive_principal_approvers_or_authorities_remove_access(self):
        self.seed()
        self.approve()
        self.visible(1)
        for principal in (TARGET, OWNER, DATA_OWNER):
            self.sql(f"UPDATE kai.ai_principals SET is_active=false WHERE principal_id='{principal}';")
            self.visible(0)
            self.sql(f"UPDATE kai.ai_principals SET is_active=true WHERE principal_id='{principal}';")
            self.visible(1)
        for principal in (OWNER, DATA_OWNER):
            self.sql(f"UPDATE kai.warehouse_approval_authorities SET is_active=false WHERE principal_id='{principal}';")
            self.visible(0)
            self.sql(f"UPDATE kai.warehouse_approval_authorities SET is_active=true WHERE principal_id='{principal}';")
            self.visible(1)

    def test_expiry_revocation_future_approvals_and_pending_binding_deny(self):
        self.seed()
        self.approve()
        for assignment, reset in (
            ("expires_at=now()-interval '1 second'", "expires_at=NULL"),
            ("revoked_at=now()", "revoked_at=NULL"),
            ("owner_approved_at=now()+interval '1 hour'", "owner_approved_at=now()"),
            ("data_owner_approved_at=now()+interval '1 hour'", "data_owner_approved_at=now()"),
        ):
            self.sql(f"UPDATE kai.approved_ch_accounts SET {assignment};")
            self.visible(0)
            self.sql(f"UPDATE kai.approved_ch_accounts SET {reset};")
            self.visible(1)
        self.sql("UPDATE kai.ch_bindings SET status='pending';")
        self.visible(0)

    def test_revocation_requires_ciphertext_wipe_and_hides_binding(self):
        self.seed()
        self.approve()
        self.assertNotEqual(self.sql("UPDATE kai.ch_bindings SET status='revoked'", check=False).returncode, 0)
        self.sql("UPDATE kai.ch_bindings SET status='revoked',cipher_blob=NULL,key_version=NULL;")
        self.visible(0)
        self.assertEqual(self.sql("SELECT cipher_blob IS NULL AND key_version IS NULL FROM kai.ch_bindings").stdout.strip(), 't')

    def test_identity_approval_and_ciphertext_constraints(self):
        self.seed()
        for statement in (
            f"INSERT INTO kai.ai_principals VALUES ('{BINDING}','librechat-local','user-1',NULL,true,now())",
            f"INSERT INTO kai.ai_principals (principal_id,issuer,subject) VALUES ('{BINDING}','  ','x')",
            f"INSERT INTO kai.ai_principals (principal_id,issuer,subject,librechat_user_id) VALUES ('{BINDING}','other','x','user-1')",
            f"INSERT INTO kai.approved_ch_accounts (principal_id,ch_username) VALUES ('{OWNER}','personal_alice')",
            f"UPDATE kai.approved_ch_accounts SET owner_approved_by='{OWNER}'",
            f"UPDATE kai.approved_ch_accounts SET owner_approved_by='{OWNER}',owner_approved_at=now(),data_owner_approved_by='{OWNER}',data_owner_approved_at=now()",
            f"INSERT INTO kai.warehouse_approval_authorities (principal_id,approval_role) VALUES ('{OWNER}','ADMIN')",
            "UPDATE kai.ch_bindings SET verified_at=NULL",
            "UPDATE kai.ch_bindings SET key_version=0",
            "UPDATE kai.ch_bindings SET cipher_blob=decode('00','hex')",
            "UPDATE kai.ch_bindings SET ch_username='unapproved'",
            "DELETE FROM kai.ai_principals",
        ):
            with self.subTest(statement=statement):
                self.assertNotEqual(self.sql(statement, check=False).returncode, 0)

    def test_mcp_role_reads_only_eligible_view_and_has_no_mutation_or_ddl(self):
        self.seed()
        self.execute("\\ir /migrations/create_binding_roles.sql\n", database=self.database)
        self.execute("\\ir /migrations/create_binding_roles.sql\n", database=self.database)
        self.sql("\\ir /migrations/grant_mcp_bindings.sql\n")
        self.visible(0, role='kai_mcp')
        self.approve()
        self.visible(1, role='kai_mcp')
        for statement in (
            "SELECT * FROM kai.ch_bindings", "SELECT * FROM kai.approved_ch_accounts",
            "SELECT * FROM kai.ai_principals", "SELECT * FROM kai.warehouse_approval_authorities",
            "SELECT * FROM kai.schema_migrations", "DELETE FROM kai.active_ch_bindings",
            "UPDATE kai.ch_bindings SET status='revoked'", "CREATE TABLE kai.injected (x int)",
            "DROP VIEW kai.active_ch_bindings", "SET ROLE kai_migrator",
        ):
            with self.subTest(statement=statement):
                self.assertNotEqual(self.sql(statement, check=False, role='kai_mcp').returncode, 0)
        self.sql("UPDATE kai.approved_ch_accounts SET revoked_at=now();")
        self.visible(0, role='kai_mcp')

    def test_privileged_existing_mcp_role_is_refused(self):
        self.migrate()
        self.execute("\\ir /migrations/create_binding_roles.sql\n", database=self.database)
        self.execute("ALTER ROLE kai_mcp CREATEDB;")
        try:
            result = self.execute("\\ir /migrations/create_binding_roles.sql\n", database=self.database, check=False)
            self.assertNotEqual(result.returncode, 0)
        finally:
            self.execute("ALTER ROLE kai_mcp NOCREATEDB;")

    def test_existing_mcp_base_table_grants_are_refused(self):
        self.migrate()
        self.execute("\\ir /migrations/create_binding_roles.sql\n", database=self.database)
        self.sql("GRANT SELECT ON kai.ch_bindings TO kai_mcp;")
        self.assertNotEqual(self.sql("\\ir /migrations/grant_mcp_bindings.sql\n", check=False).returncode, 0)

    def test_existing_view_column_grant_option_is_refused(self):
        self.migrate()
        self.execute("\\ir /migrations/create_binding_roles.sql\n", database=self.database)
        self.sql("GRANT SELECT(cipher_blob) ON kai.active_ch_bindings TO kai_mcp WITH GRANT OPTION;")
        self.assertNotEqual(self.sql("\\ir /migrations/grant_mcp_bindings.sql\n", check=False).returncode, 0)

    def test_publicly_callable_definer_function_is_refused(self):
        self.migrate()
        self.execute("\\ir /migrations/create_binding_roles.sql\n", database=self.database)
        self.sql("CREATE FUNCTION kai.unsafe_binding_count() RETURNS bigint LANGUAGE sql SECURITY DEFINER "
                 "AS $$ SELECT count(*) FROM kai.ch_bindings $$;")
        self.assertNotEqual(self.sql("\\ir /migrations/grant_mcp_bindings.sql\n", check=False).returncode, 0)

    def test_approved_identity_cannot_be_reassigned_to_another_login(self):
        self.seed()
        self.approve()
        for assignment in ("issuer='another-issuer'", "subject='another-login'", "librechat_user_id='another-user'"):
            self.assertNotEqual(self.sql(f"UPDATE kai.ai_principals SET {assignment} WHERE principal_id='{TARGET}'", check=False).returncode, 0)
        self.visible(1)
        self.sql("DELETE FROM kai.ch_bindings;")
        for assignment in (f"principal_id='{OWNER}'", "ch_username='another-account'"):
            self.assertNotEqual(self.sql(f"UPDATE kai.approved_ch_accounts SET {assignment}", check=False).returncode, 0)

    def test_runtime_cannot_migrate_or_take_schema_ownership(self):
        self.migrate()
        self.assertNotEqual(self.migrate(check=False, role='kai_runtime').returncode, 0)
        self.assertEqual(self.sql("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                  "WHERE n.nspname='kai' AND c.relowner <> 'kai_migrator'::regrole").stdout.strip(), '0')


if __name__ == '__main__':
    unittest.main()
