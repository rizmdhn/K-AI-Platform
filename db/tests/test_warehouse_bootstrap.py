"""Operator scripts run only against the disposable, network-isolated PostgreSQL fixture."""
import unittest
from support import DB_DIR, LocalPostgresTestCase


class WarehouseBootstrapTests(LocalPostgresTestCase):
    def setUp(self):
        super().setUp()
        self.sql('\\ir /migrations/migrate_identity.sql\n\\ir /migrations/migrate_approvals.sql\n\\ir /migrations/migrate_enrollment.sql\n\\ir /migrations/migrate_admin_warehouse.sql\n')
        for script in ('create_binding_roles.sql', 'create_enrollment_role.sql'):
            self.execute(f'\\ir /migrations/{script}\n')
        for script in ('grant_mcp_bindings.sql', 'grant_enrollment_access.sql'):
            self.sql(f'\\ir /migrations/{script}\n')
        self.addCleanup(self.cleanup_roles)

    def cleanup_roles(self):
        # Each test has its own database, while login roles are cluster-wide.
        self.execute(f'DROP DATABASE {self.database} WITH (FORCE);')
        self.execute('DROP ROLE IF EXISTS kai_binding_runtime, kai_approval_runtime, kai_enrollment_runtime;')

    def provision(self, check=True):
        self.assertTrue((DB_DIR / 'provision_warehouse_runtime.sql').exists(), 'Runtime bootstrap is not implemented')
        return self.execute('\\ir /migrations/provision_warehouse_runtime.sql\n', self.database, check)

    def test_profiles_are_minimal_and_rerun_preserves_login_and_password(self):
        self.provision()
        self.assertEqual(self.execute("SELECT count(*) FROM pg_roles WHERE rolname='kai_approval_runtime';").stdout.strip(), '0')
        self.assertEqual(self.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('kai_binding_runtime','kai_approval_runtime','kai_enrollment_runtime') AND NOT rolcanlogin AND rolinherit AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls);", self.database).stdout.strip(), '2')
        self.execute("ALTER ROLE kai_binding_runtime LOGIN PASSWORD 'md50123456789abcdef0123456789abcdef';")
        password_before = self.execute("SELECT rolpassword FROM pg_authid WHERE rolname='kai_binding_runtime';").stdout
        self.provision()
        self.assertEqual(self.execute("SELECT rolpassword FROM pg_authid WHERE rolname='kai_binding_runtime';").stdout, password_before)
        self.assertEqual(self.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname='kai_binding_runtime';").stdout.strip(), 't')
        self.assertEqual(self.execute("SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname LIKE 'kai_%_runtime';").stdout.strip(), '2')
        self.assertNotEqual(self.sql('SELECT * FROM kai.ch_bindings;', role='kai_binding_runtime', check=False).returncode, 0)
        self.assertEqual(self.sql('SELECT count(*) FROM kai.active_ch_bindings;', role='kai_binding_runtime').stdout.strip(), '0')

    def test_existing_legacy_approval_runtime_is_untouched(self):
        self.execute("CREATE ROLE kai_approval_runtime LOGIN PASSWORD 'legacy-fixture-password';")
        before=self.execute("SELECT rolcanlogin,rolpassword FROM pg_authid WHERE rolname='kai_approval_runtime';").stdout
        self.provision()
        self.assertEqual(self.execute("SELECT rolcanlogin,rolpassword FROM pg_authid WHERE rolname='kai_approval_runtime';").stdout,before)
        self.assertEqual(self.execute("SELECT count(*) FROM pg_auth_members WHERE member='kai_approval_runtime'::regrole;").stdout.strip(),'0')

    def test_privileged_existing_profile_rejects_and_rolls_back_new_roles(self):
        self.execute('CREATE ROLE kai_enrollment_runtime CREATEDB;')
        self.assertNotEqual(self.provision(check=False).returncode, 0)
        self.assertEqual(self.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('kai_binding_runtime','kai_approval_runtime');").stdout.strip(), '0')

    def test_other_memberships_and_admin_option_rejected(self):
        self.provision()
        self.execute('GRANT kai_enrollment_api TO kai_binding_runtime;')
        self.assertNotEqual(self.provision(check=False).returncode, 0)
        self.execute('REVOKE kai_enrollment_api FROM kai_binding_runtime; GRANT kai_mcp TO kai_binding_runtime WITH ADMIN OPTION;')
        self.assertNotEqual(self.provision(check=False).returncode, 0)

    def test_ownership_and_direct_table_column_function_grants_rejected(self):
        self.provision()
        for grant, revoke in (
            ('GRANT SELECT ON kai.ai_principals TO kai_binding_runtime;', 'REVOKE SELECT ON kai.ai_principals FROM kai_binding_runtime;'),
            ('GRANT SELECT(subject) ON kai.ai_principals TO kai_binding_runtime;', 'REVOKE SELECT(subject) ON kai.ai_principals FROM kai_binding_runtime;'),
            ('GRANT EXECUTE ON FUNCTION kai.warehouse_ready(uuid,text) TO kai_binding_runtime;', 'REVOKE EXECUTE ON FUNCTION kai.warehouse_ready(uuid,text) FROM kai_binding_runtime;'),
            ("CREATE TYPE kai.fixture_type AS ENUM ('safe'); GRANT USAGE ON TYPE kai.fixture_type TO kai_binding_runtime;", 'DROP TYPE kai.fixture_type;'),
            ('CREATE TABLE kai.owned_fixture(id int); ALTER TABLE kai.owned_fixture OWNER TO kai_binding_runtime;', 'DROP TABLE kai.owned_fixture;')):
            self.execute(grant, self.database)
            self.assertNotEqual(self.provision(check=False).returncode, 0, grant)
            self.sql(revoke)



if __name__ == '__main__':
    unittest.main()
