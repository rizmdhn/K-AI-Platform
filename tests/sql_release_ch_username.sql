-- Migration 010 check against a local kai-metadata database. Runs inside a transaction and always rolls back.
-- Run: docker exec -i <metadata container> sh -ec 'export PGPASSWORD="$POSTGRES_PASSWORD"; psql -X -h /var/run/postgresql -U postgres -d kai_metadata -v ON_ERROR_STOP=1' < tests/sql_release_ch_username.sql
BEGIN;
DO $$
DECLARE a TEXT := 'aaaaaaaaaaaaaaaaaaaaaaaa'; old TEXT := 'eeeeeeeeeeeeeeeeeeeeeeee'; new TEXT := 'ffffffffffffffffffffffff';
  svc TEXT := 'https://ch.example:8443'; iss TEXT := 'sql-test-issuer'; refused BOOLEAN; pid UUID;
BEGIN
  PERFORM kai.warehouse_prepare(a, old, 'shared_ch', svc, iss, gen_random_uuid());
  SELECT principal_id INTO pid FROM kai.ai_principals WHERE issuer = iss AND subject = old;
  UPDATE kai.ch_bindings SET cipher_blob = decode(repeat('ab', 40), 'hex'), key_version = 1, status = 'active', verified_at = clock_timestamp() WHERE principal_id = pid;
  -- Release is refused while the binding is not revoked.
  refused := false;
  BEGIN PERFORM kai.warehouse_release(a, old, iss, gen_random_uuid()); EXCEPTION WHEN OTHERS THEN refused := true; END;
  IF NOT refused THEN RAISE EXCEPTION 'released an active binding'; END IF;
  PERFORM kai.warehouse_revoke(a, old, iss, gen_random_uuid());
  -- Before release another principal cannot take the username.
  refused := false;
  BEGIN PERFORM kai.warehouse_prepare(a, new, 'shared_ch', svc, iss, gen_random_uuid()); EXCEPTION WHEN OTHERS THEN refused := true; END;
  IF NOT refused THEN RAISE EXCEPTION 'revoked username reused without release'; END IF;
  PERFORM kai.warehouse_release(a, old, iss, gen_random_uuid());
  PERFORM kai.warehouse_prepare(a, new, 'shared_ch', svc, iss, gen_random_uuid());
  IF (SELECT ch_username FROM kai.ch_bindings b JOIN kai.ai_principals p USING (principal_id) WHERE p.issuer = iss AND p.subject = new) <> 'shared_ch' THEN
    RAISE EXCEPTION 'new principal did not get the released username';
  END IF;
  -- The old binding stays revoked and its owner stays fenced.
  IF (SELECT status FROM kai.ch_bindings WHERE principal_id = pid) <> 'revoked' THEN RAISE EXCEPTION 'old binding changed'; END IF;
  refused := false;
  BEGIN PERFORM kai.warehouse_prepare(a, old, 'other_ch', svc, iss, gen_random_uuid()); EXCEPTION WHEN OTHERS THEN refused := true; END;
  IF NOT refused THEN RAISE EXCEPTION 'fence lifted by release'; END IF;
  IF NOT EXISTS (SELECT 1 FROM kai.binding_enrollment_events WHERE action = 'release' AND target_principal = pid) THEN RAISE EXCEPTION 'release not audited'; END IF;
  RAISE NOTICE 'migration 010 checks passed';
END $$;
ROLLBACK;
