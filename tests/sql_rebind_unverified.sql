-- Migration 008 check against a local kai-metadata database. Runs inside a transaction and always rolls back.
-- Run: docker exec -i <metadata container> sh -ec 'export PGPASSWORD="$POSTGRES_PASSWORD"; psql -X -h /var/run/postgresql -U postgres -d kai_metadata -v ON_ERROR_STOP=1' < tests/sql_rebind_unverified.sql
BEGIN;
DO $$
DECLARE s TEXT := 'bbbbbbbbbbbbbbbbbbbbbbbb'; a TEXT := 'aaaaaaaaaaaaaaaaaaaaaaaa'; svc TEXT := 'https://ch.example:8443'; iss TEXT := 'sql-test-issuer';
  pid UUID; got TEXT; refused BOOLEAN := false;
BEGIN
  PERFORM kai.warehouse_prepare(a, s, 'typo_user', svc, iss, gen_random_uuid());
  -- First credential never verified: a different username must be accepted and the old mapping released.
  PERFORM kai.warehouse_prepare(a, s, 'right_user', svc, iss, gen_random_uuid());
  SELECT principal_id INTO pid FROM kai.ai_principals WHERE issuer = iss AND subject = s;
  SELECT ch_username INTO got FROM kai.ch_bindings WHERE principal_id = pid;
  IF got IS DISTINCT FROM 'right_user' THEN RAISE EXCEPTION 'retarget failed: %', got; END IF;
  IF EXISTS (SELECT 1 FROM kai.approved_ch_accounts WHERE ch_username = 'typo_user') THEN RAISE EXCEPTION 'old mapping not released'; END IF;
  -- Once a credential is saved and verified, the username is locked again.
  UPDATE kai.ch_bindings SET cipher_blob = decode(repeat('ab', 40), 'hex'), key_version = 1, status = 'active', verified_at = clock_timestamp() WHERE principal_id = pid;
  BEGIN
    PERFORM kai.warehouse_prepare(a, s, 'third_user', svc, iss, gen_random_uuid());
  EXCEPTION WHEN OTHERS THEN refused := true;
  END;
  IF NOT refused THEN RAISE EXCEPTION 'active binding was retargeted'; END IF;
  -- A username owned by another principal is never taken over.
  PERFORM kai.warehouse_prepare(a, 'cccccccccccccccccccccccc', 'other_user', svc, iss, gen_random_uuid());
  refused := false;
  BEGIN
    PERFORM kai.warehouse_prepare(a, 'dddddddddddddddddddddddd', 'first_try', svc, iss, gen_random_uuid());
    PERFORM kai.warehouse_prepare(a, 'dddddddddddddddddddddddd', 'other_user', svc, iss, gen_random_uuid());
  EXCEPTION WHEN OTHERS THEN refused := true;
  END;
  IF NOT refused THEN RAISE EXCEPTION 'took over another principal''s username'; END IF;
  RAISE NOTICE 'migration 008 checks passed';
END $$;
ROLLBACK;
