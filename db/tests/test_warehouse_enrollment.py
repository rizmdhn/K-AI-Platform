"""Native-verified identity input is supplied by the service; SQL enforces binding state/races."""
import unittest
from concurrent.futures import ThreadPoolExecutor
import time
from support import DB_DIR, LocalPostgresTestCase


class WarehouseEnrollmentTests(LocalPostgresTestCase):
    def setUp(self):
        super().setUp()
        self.sql('\\ir /migrations/migrate_identity.sql\n\\ir /migrations/migrate_approvals.sql\n')
        self.assertTrue((DB_DIR / 'migrate_enrollment.sql').exists(), 'Enrollment operations are not implemented')
        self.sql('\\ir /migrations/migrate_enrollment.sql\n')
        self.sql('\\ir /migrations/migrate_admin_warehouse.sql\n')
        self.sql('\\ir /migrations/migrate_revocation_fence.sql\n')
        self.execute('\\ir /migrations/create_enrollment_role.sql\n')
        self.sql('\\ir /migrations/grant_enrollment_access.sql\n')
        self.owner, self.dba, self.target = 'a'*24, 'b'*24, 'c'*24
        self.prepare()

    def prepare(self, username='tester_ro', service='https://staging.example:8443', check=True):
        return self.sql(f"SELECT kai.warehouse_prepare('{self.owner}','{self.target}','{username}','{service}','fixture',gen_random_uuid());", check=check)

    def status(self):
        return self.sql(f"SELECT kai.warehouse_status('{self.owner}','{self.target}','tester_ro','fixture');").stdout

    def save(self, revision=None, check=True, actor=None):
        revision = "'"+revision+"'::uuid" if revision else '(SELECT revision FROM kai.ch_bindings)'
        return self.sql(f"""SELECT kai.warehouse_save('{actor or self.owner}','{self.target}','tester_ro',
          'https://staging.example:8443','fixture',(SELECT binding_id FROM kai.ch_bindings),
          {revision},decode(repeat('ab',40),'hex'),1,gen_random_uuid());""",check=check)

    def test_prepare_is_idempotent_without_authority_escalation_or_approval(self):
        self.prepare()
        self.assertEqual(self.sql('SELECT count(*) FROM kai.ch_bindings;').stdout.strip(),'1')
        self.assertIn('ready', self.status())
        self.assertNotIn('cipher_blob', self.status())
        self.assertEqual(self.sql('SELECT count(*) FROM kai.active_ch_bindings;').stdout.strip(),'0')

    def test_no_approval_required_and_active_view_has_target(self):
        import json
        self.assertEqual(json.loads(self.save().stdout)['state'],'active')
        self.assertIn('active',self.status())
        self.assertEqual(self.sql('SELECT service_target FROM kai.active_ch_bindings;').stdout.strip(),'https://staging.example:8443')

    def test_rotation_fences_old_revision_and_audits_without_secret(self):
        revision=self.sql('SELECT revision FROM kai.ch_bindings;').stdout.strip()
        self.save(revision)
        self.assertNotEqual(self.save(revision,check=False).returncode,0)
        self.save()
        self.assertEqual(self.sql("SELECT count(*) FROM kai.binding_enrollment_events WHERE action='save';").stdout.strip(),'2')

    def test_revoke_wipes_secret_and_cannot_resurrect(self):
        self.save()
        self.sql(f"SELECT kai.warehouse_revoke('{self.owner}','{self.target}','fixture',gen_random_uuid());")
        self.assertEqual(self.sql('SELECT status,cipher_blob IS NULL,key_version IS NULL FROM kai.ch_bindings;').stdout.strip(),'revoked|t|t')
        self.assertNotEqual(self.save(check=False).returncode,0)
        self.assertNotEqual(self.prepare(check=False).returncode,0)
        self.assertEqual(self.sql('SELECT count(*) FROM kai.active_ch_bindings;').stdout.strip(),'0')

    def test_unregistered_native_admin_can_revoke_an_existing_binding(self):
        self.save()
        actor='f'*24
        self.sql(f"SELECT kai.warehouse_revoke('{actor}','{self.target}','fixture',gen_random_uuid());")
        self.assertEqual(self.sql('SELECT status,cipher_blob IS NULL,key_version IS NULL FROM kai.ch_bindings;').stdout.strip(),'revoked|t|t')
        self.assertEqual(self.sql(f"SELECT count(*) FROM kai.binding_enrollment_events e JOIN kai.ai_principals p ON p.principal_id=e.actor_principal WHERE p.subject='{actor}' AND e.action='revoke';").stdout.strip(),'1')

    def test_existing_inactive_actor_cannot_revoke(self):
        self.save()
        actor='f'*24
        self.sql(f"INSERT INTO kai.ai_principals(principal_id,issuer,subject,librechat_user_id,is_active) VALUES(gen_random_uuid(),'fixture','{actor}','{actor}',false);")
        self.assertNotEqual(self.sql(f"SELECT kai.warehouse_revoke('{actor}','{self.target}','fixture',gen_random_uuid());",check=False).returncode,0)
        self.assertEqual(self.sql('SELECT status FROM kai.ch_bindings;').stdout.strip(),'active')

    def test_target_change_and_username_theft_are_denied(self):
        self.assertNotEqual(self.prepare(service='https://production.example:8443',check=False).returncode,0)
        self.assertNotEqual(self.prepare(username='other',check=False).returncode,0)
        result=self.sql(f"SELECT kai.warehouse_prepare('{self.owner}','{'d'*24}','tester_ro','https://staging.example:8443','fixture',gen_random_uuid());",check=False)
        self.assertNotEqual(result.returncode,0)

    def test_disabled_principal_and_revoked_mapping_cannot_activate(self):
        self.sql("UPDATE kai.ai_principals SET is_active=false WHERE subject='"+self.target+"';")
        self.assertNotEqual(self.save(check=False).returncode,0)
        self.sql("UPDATE kai.ai_principals SET is_active=true; UPDATE kai.approved_ch_accounts SET revoked_at=now();")
        self.assertNotEqual(self.save(check=False).returncode,0)

    def test_status_has_no_approval_fields_and_upgrade_is_idempotent(self):
        import json
        before=json.loads(self.status())
        self.assertFalse(any('approv' in key for key in before))
        self.assertFalse(before['credentials_saved'])
        self.sql('\\ir /migrations/migrate_admin_warehouse.sql\n')
        self.sql('\\ir /migrations/migrate_revocation_fence.sql\n')
        self.assertEqual(json.loads(self.status()),before)
        self.assertEqual(self.sql("SELECT count(*) FROM kai.schema_migrations WHERE version='004_admin_managed_warehouse';").stdout.strip(),'1')
        for table in ('warehouse_approval_authorities','access_approval_events','approved_ch_accounts'):
            self.assertEqual(self.sql(f"SELECT to_regclass('kai.{table}') IS NOT NULL;").stdout.strip(),'t')

    def test_active_view_rechecks_target_and_mapping_eligibility(self):
        self.save()
        for disable, restore in (
            ("UPDATE kai.ai_principals SET is_active=false WHERE subject='"+self.target+"';", "UPDATE kai.ai_principals SET is_active=true;"),
            ("UPDATE kai.ch_bindings SET verified_at=now()+interval '1 day';", "UPDATE kai.ch_bindings SET verified_at=now();")):
            self.sql(disable)
            self.assertEqual(self.sql('SELECT count(*) FROM kai.active_ch_bindings;').stdout.strip(),'0')
            self.sql(restore)
            self.assertEqual(self.sql('SELECT count(*) FROM kai.active_ch_bindings;').stdout.strip(),'1')

    def test_status_allows_unregistered_native_actor_but_denies_inactive_actor(self):
        actor='f'*24
        result=self.sql(f"SELECT kai.warehouse_status('{actor}','{'e'*24}','new_ro','fixture');").stdout
        self.assertIn('not_prepared',result)
        self.sql(f"INSERT INTO kai.ai_principals(principal_id,issuer,subject,librechat_user_id,is_active) VALUES(gen_random_uuid(),'fixture','{actor}','{actor}',false);")
        self.assertNotEqual(self.sql(f"SELECT kai.warehouse_status('{actor}','{self.target}','tester_ro','fixture');",check=False).returncode,0)

    def test_runtime_only_executes_allowlisted_operations(self):
        self.execute('GRANT kai_enrollment_api TO kai_runtime;')
        self.assertIn('ready', self.sql(f"SELECT kai.warehouse_status('{self.owner}','{self.target}','tester_ro','fixture');",role='kai_runtime').stdout)
        for sql in ('SELECT * FROM kai.ch_bindings;', 'UPDATE kai.ch_bindings SET status=\'active\';',
                    'CREATE TABLE kai.forbidden(value text);', 'SELECT kai.record_account_approval(NULL,NULL,NULL,NULL,NULL,NULL,NULL);'):
            self.assertNotEqual(self.sql(sql,role='kai_runtime',check=False).returncode,0)

    def test_legacy_unknown_service_can_still_be_revoked(self):
        self.save()
        self.sql('UPDATE kai.ch_bindings SET service_target=NULL;')
        self.sql(f"SELECT kai.warehouse_revoke('{self.owner}','{self.target}','fixture',gen_random_uuid());")
        self.assertEqual(self.sql('SELECT status,cipher_blob IS NULL FROM kai.ch_bindings;').stdout.strip(),'revoked|t')

    def test_actor_deactivated_while_waiting_for_binding_lock_cannot_save(self):
        actor='e'*24
        self.sql(f"SELECT kai.warehouse_prepare('{actor}','{self.target}','tester_ro','https://staging.example:8443','fixture',gen_random_uuid());")
        with ThreadPoolExecutor(max_workers=1) as pool:
            holding=pool.submit(self.sql,f"""BEGIN; SELECT binding_id FROM kai.ch_bindings FOR UPDATE;
                UPDATE kai.ai_principals SET is_active=false WHERE subject='{actor}';
                SELECT pg_advisory_lock(837410); SELECT pg_sleep(1.5); COMMIT;""")
            for _ in range(100):
                if self.sql("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objid=837410;").stdout.strip()=='1':break
                time.sleep(.01)
            else:self.fail('Lock fixture did not become ready')
            result=self.save(actor=actor,check=False)
            holding.result()
        self.assertNotEqual(result.returncode,0,'Deactivated actor completed enrollment after a lock wait')


class AdminWarehouseUpgradeTests(LocalPostgresTestCase):
    def test_existing_003_binding_history_and_grants_survive_upgrade(self):
        self.sql('\\ir /migrations/migrate_identity.sql\n\\ir /migrations/migrate_approvals.sql\n\\ir /migrations/migrate_enrollment.sql\n')
        self.execute('\\ir /migrations/create_binding_roles.sql\n')
        self.sql('\\ir /migrations/grant_mcp_bindings.sql\n')
        self.sql("SELECT kai.warehouse_prepare(repeat('a',24),repeat('b',24),'existing_ro','https://fixture.invalid:8443','fixture',gen_random_uuid());")
        self.sql("UPDATE kai.ch_bindings SET status='active',cipher_blob=decode(repeat('ab',40),'hex'),key_version=1,verified_at=now(); UPDATE kai.approved_ch_accounts SET expires_at=now()-interval '1 day';")
        before=self.sql("SELECT binding_id,revision,encode(cipher_blob,'hex') FROM kai.ch_bindings;").stdout
        self.assertEqual(self.sql('SELECT count(*) FROM kai.active_ch_bindings;').stdout.strip(),'0')
        self.sql('\\ir /migrations/migrate_admin_warehouse.sql\n\\ir /migrations/migrate_admin_warehouse.sql\n')
        self.assertEqual(self.sql("SELECT binding_id,revision,encode(cipher_blob,'hex') FROM kai.ch_bindings;").stdout,before)
        self.assertEqual(self.sql('SELECT count(*) FROM kai.binding_enrollment_events;').stdout.strip(),'1')
        self.assertEqual(self.sql('SELECT count(*) FROM kai.active_ch_bindings;',role='kai_mcp').stdout.strip(),'1')


if __name__ == '__main__': unittest.main()
