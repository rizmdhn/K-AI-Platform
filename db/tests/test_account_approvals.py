"""Historical 000/002 compatibility only; current enrollment does not use approval authority."""
import json
import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from support import LocalPostgresTestCase


class AccountApprovalTests(LocalPostgresTestCase):
    def setUp(self):
        super().setUp()
        self.sql('\\ir /migrations/migrate_identity.sql\n\\ir /migrations/migrate_approvals.sql\n')
        self.execute('\\ir /migrations/create_approval_role.sql\n', self.database)
        self.sql('\\ir /migrations/grant_approval_access.sql\n')
        for number, letter in enumerate('abcde', 1):
            self.sql(f"INSERT INTO kai.ai_principals(principal_id,issuer,subject,librechat_user_id) VALUES ('00000000-0000-0000-0000-{number:012d}','librechat-local','{letter*24}','{letter*24}');")
        self.sql("INSERT INTO kai.warehouse_approval_authorities(principal_id,approval_role) VALUES "
                 "('00000000-0000-0000-0000-000000000001','platform_owner'),"
                 "('00000000-0000-0000-0000-000000000002','data_owner'),"
                 "('00000000-0000-0000-0000-000000000003','platform_owner');")

    def approve(self, actor='a', target='d', category='platform_owner', account='personal_target', expiry='NULL', check=True):
        return self.sql("\\set VERBOSITY verbose\nSELECT kai.record_account_approval("
            f"'{actor*24}','{target*24}','{account}','{category}',{expiry},'librechat-local','{uuid.uuid4()}');",
            role='kai_approval_api', check=check)

    def count(self, table):
        return int(self.sql(f'SELECT count(*) FROM kai.{table}').stdout.strip())

    def test_two_categories_are_distinct_and_idempotent_with_atomic_audit(self):
        first = json.loads(self.approve().stdout)
        self.assertFalse(first['both_approvals_recorded'])
        self.assertTrue(json.loads(self.approve().stdout)['already_recorded'])
        second = json.loads(self.approve(actor='b',category='data_owner').stdout)
        self.assertTrue(second['both_approvals_recorded'])
        self.assertEqual(self.count('access_approval_events'), 2)
        self.assertEqual(self.count('ch_bindings'), 0)

    def test_no_authority_inactive_actor_wrong_category_and_same_actor_twice_deny(self):
        for actor, category in [('d','platform_owner'),('a','data_owner')]:
            result = self.approve(actor=actor,category=category,check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('K0001', result.stderr)
        self.sql("UPDATE kai.ai_principals SET is_active=false WHERE subject=repeat('a',24)")
        self.assertNotEqual(self.approve(check=False).returncode, 0)
        self.sql("UPDATE kai.ai_principals SET is_active=true WHERE subject=repeat('a',24);")
        self.approve()
        self.sql("INSERT INTO kai.warehouse_approval_authorities(principal_id,approval_role) VALUES ('00000000-0000-0000-0000-000000000001','data_owner')")
        result=self.approve(category='data_owner',check=False)
        self.assertIn('K0002', result.stderr)
        self.assertEqual(self.count('access_approval_events'), 1)

    def test_existing_consent_target_and_expiry_cannot_be_replaced(self):
        self.approve()
        for kwargs in ({'actor':'c'}, {'target':'e'}, {'expiry':"now()+interval '1 day'"}):
            result=self.approve(check=False,**kwargs)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('K0002', result.stderr)
        self.assertEqual(self.count('access_approval_events'),1)
        self.sql("UPDATE kai.approved_ch_accounts SET revoked_at=now()")
        self.assertNotEqual(self.approve(check=False).returncode,0)
        self.sql("UPDATE kai.approved_ch_accounts SET revoked_at=NULL,expires_at=now()-interval '1 second'")
        self.assertNotEqual(self.approve(check=False).returncode,0)

    def test_parallel_decisions_and_duplicate_retries_preserve_one_event_per_consent(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _:self.approve(),range(2)))
        self.assertEqual(self.count('access_approval_events'),1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _:self.approve(actor='b',category='data_owner'),range(2)))
        self.assertEqual(self.count('access_approval_events'),2)

    def test_audit_failure_rolls_back_mapping_and_consent(self):
        self.sql("CREATE FUNCTION kai.reject_event() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture audit failure'; END $$;"
                 "CREATE TRIGGER reject_event BEFORE INSERT ON kai.access_approval_events FOR EACH ROW EXECUTE FUNCTION kai.reject_event();")
        self.assertNotEqual(self.approve(check=False).returncode,0)
        self.assertEqual(self.count('approved_ch_accounts'),0)
        self.assertEqual(self.count('access_approval_events'),0)

    def test_writer_only_executes_fixed_operation_and_cannot_read_secrets_or_change_authority(self):
        self.approve()
        for sql in ('SELECT * FROM kai.ch_bindings','SELECT * FROM kai.access_approval_events',
                    'UPDATE kai.warehouse_approval_authorities SET is_active=true',
                    'DELETE FROM kai.approved_ch_accounts','CREATE TABLE kai.injected(x int)',
                    "UPDATE kai.ai_principals SET is_active=false",'SET ROLE kai_migrator'):
            self.assertNotEqual(self.sql(sql,role='kai_approval_api',check=False).returncode,0)
        self.assertNotEqual(self.approve(expiry="now()-interval '1 day'",check=False).returncode,0)

    def test_migration_repeat_and_public_execute_denial(self):
        self.sql('\\ir /migrations/migrate_approvals.sql\n')
        result=self.sql("SELECT kai.record_account_approval(repeat('a',24),repeat('d',24),'personal_target','platform_owner',NULL,'librechat-local',gen_random_uuid())",role='kai_runtime',check=False)
        self.assertNotEqual(result.returncode,0)

    def wait_for_lock_holder(self):
        for _ in range(100):
            active=self.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name='approval-lock-fixture' AND wait_event='PgSleep'",self.database)
            if active.stdout.strip()=='1':
                return
            time.sleep(.02)
        self.fail('fixture row lock was not established')

    def test_expiry_is_rechecked_after_waiting_for_mapping_lock(self):
        self.sql("INSERT INTO kai.approved_ch_accounts(principal_id,ch_username,expires_at) VALUES('00000000-0000-0000-0000-000000000004','personal_target',clock_timestamp()+interval '3 seconds')")
        expiry=self.sql("SELECT quote_literal(expires_at::text) FROM kai.approved_ch_accounts").stdout.strip()
        with ThreadPoolExecutor(max_workers=1) as pool:
            locked=pool.submit(self.sql,"SET application_name='approval-lock-fixture'; BEGIN; SELECT principal_id FROM kai.approved_ch_accounts FOR UPDATE; SELECT pg_sleep(4); COMMIT;")
            self.wait_for_lock_holder()
            result=self.approve(expiry=expiry,check=False)
            locked.result()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('K0002',result.stderr)
        self.assertEqual(self.count('access_approval_events'),0)

    def test_authority_revocation_during_lock_wait_denies_decision(self):
        self.sql("INSERT INTO kai.approved_ch_accounts(principal_id,ch_username) VALUES('00000000-0000-0000-0000-000000000004','personal_target')")
        with ThreadPoolExecutor(max_workers=1) as pool:
            locked=pool.submit(self.sql,"SET application_name='approval-lock-fixture'; BEGIN; SELECT principal_id FROM kai.approved_ch_accounts FOR UPDATE; SELECT pg_sleep(1); UPDATE kai.warehouse_approval_authorities SET is_active=false; SELECT pg_sleep(1); COMMIT;")
            self.wait_for_lock_holder()
            result=self.approve(check=False)
            locked.result()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('K0001',result.stderr)
        self.assertEqual(self.count('access_approval_events'),0)
