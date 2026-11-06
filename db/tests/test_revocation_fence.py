import time
from concurrent.futures import ThreadPoolExecutor
from support import DB_DIR, LocalPostgresTestCase

class RevocationFenceTests(LocalPostgresTestCase):
    def setUp(self):
        super().setUp()
        for name in ('identity','approvals','enrollment','admin_warehouse'):
            self.sql('\\ir /migrations/migrate_'+name+'.sql\n')
        self.assertTrue((DB_DIR/'migrate_revocation_fence.sql').exists(), 'Durable no-binding revocation fence missing')
        self.sql('\\ir /migrations/migrate_revocation_fence.sql\n')
        self.actor='a'*24;self.target='c'*24
    def prepare(self,check=True):
        return self.sql(f"SELECT kai.warehouse_prepare('{self.actor}','{self.target}','tester_ro','https://staging.example:8443','fixture',gen_random_uuid());",check=check)
    def revoke(self):
        return self.sql(f"SELECT kai.warehouse_revoke('{self.actor}','{self.target}','fixture',gen_random_uuid());")
    def test_no_binding_revocation_is_terminal_and_idempotent(self):
        self.revoke();self.revoke()
        self.assertNotEqual(self.prepare(check=False).returncode,0)
        self.assertEqual(self.sql('SELECT count(*) FROM kai.ch_bindings;').stdout.strip(),'0')
        self.assertEqual(self.sql('SELECT count(*) FROM kai.warehouse_revoked_subjects;').stdout.strip(),'1')
        self.sql('\\ir /migrations/migrate_revocation_fence.sql\n')
    def test_existing_ciphertext_is_wiped_and_cannot_be_saved_again(self):
        self.prepare()
        self.sql("SELECT kai.warehouse_save(repeat('a',24),repeat('c',24),'tester_ro','https://staging.example:8443','fixture',(SELECT binding_id FROM kai.ch_bindings),(SELECT revision FROM kai.ch_bindings),decode(repeat('ab',40),'hex'),1,gen_random_uuid());")
        self.revoke()
        self.assertEqual(self.sql('SELECT status,cipher_blob IS NULL,key_version IS NULL FROM kai.ch_bindings;').stdout.strip(),'revoked|t|t')
        self.assertNotEqual(self.prepare(check=False).returncode,0)
        stale=self.sql("SELECT kai.warehouse_save(repeat('a',24),repeat('c',24),'tester_ro','https://staging.example:8443','fixture',(SELECT binding_id FROM kai.ch_bindings),(SELECT revision FROM kai.ch_bindings),decode(repeat('ab',40),'hex'),1,gen_random_uuid());",check=False)
        self.assertNotEqual(stale.returncode,0)
        self.assertEqual(self.sql('SELECT cipher_blob IS NULL FROM kai.ch_bindings;').stdout.strip(),'t')
    def test_prepare_authorized_before_disable_cannot_commit_after_no_binding_revoke(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            holder=pool.submit(self.sql,"BEGIN; SELECT pg_advisory_xact_lock(hashtextextended('fixture:'||repeat('c',24),0)); SELECT pg_advisory_xact_lock(837411); SELECT pg_sleep(2.0); SELECT kai.warehouse_revoke(repeat('a',24),repeat('c',24),'fixture',gen_random_uuid()); COMMIT;")
            for _ in range(50):
                if self.sql("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objid=837411;").stdout.strip()=='1':break
                time.sleep(.02)
            else:self.fail('Revocation barrier never acquired')
            pending=pool.submit(self.prepare,False)
            for _ in range(50):
                if self.sql("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND NOT granted;").stdout.strip()=='1':break
                time.sleep(.02)
            else:self.fail('Prepare never observed waiting for revocation lock')
            result=pending.result();holder.result()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.sql('SELECT count(*) FROM kai.ch_bindings;').stdout.strip(),'0')
