-- Apply through db/migrate.sql. An admin may release the ClickHouse username of a revoked binding so another
-- platform user can use it (after verifying the password again). The revoked binding, its fence and audit stay.
ALTER TABLE kai.approved_ch_accounts ADD COLUMN released_at TIMESTAMPTZ;
DO $$ DECLARE c TEXT; BEGIN
 SELECT conname INTO c FROM pg_constraint WHERE conrelid='kai.approved_ch_accounts'::regclass AND contype='u'
  AND conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid='kai.approved_ch_accounts'::regclass AND attname='ch_username')]::int2[];
 IF c IS NULL THEN RAISE EXCEPTION 'approved_ch_accounts username uniqueness not found'; END IF;
 EXECUTE format('ALTER TABLE kai.approved_ch_accounts DROP CONSTRAINT %I', c);
END $$;
CREATE UNIQUE INDEX approved_ch_accounts_unreleased_username ON kai.approved_ch_accounts(ch_username) WHERE released_at IS NULL;
ALTER TABLE kai.binding_enrollment_events DROP CONSTRAINT binding_enrollment_events_action_check,
 ADD CONSTRAINT binding_enrollment_events_action_check CHECK(action IN ('prepare','save','revoke','release'));

CREATE OR REPLACE FUNCTION kai.warehouse_prepare(p_actor TEXT,p_subject TEXT,p_username TEXT,p_service TEXT,p_issuer TEXT,p_request UUID) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE target_id UUID; actor_id UUID; mapping kai.approved_ch_accounts%ROWTYPE; b kai.ch_bindings%ROWTYPE;
BEGIN
 IF p_actor IS NULL OR p_actor !~ '^[0-9a-f]{24}$' OR p_subject IS NULL OR p_subject !~ '^[0-9a-f]{24}$'
 OR p_username IS NULL OR p_username !~ '[^[:space:]]' OR octet_length(p_username)>1024
 OR p_service IS NULL OR p_service !~ '^https://[a-z0-9.-]+:[0-9]{1,5}$' OR octet_length(p_service)>512
 OR p_issuer IS NULL OR p_issuer !~ '[^[:space:]]' OR p_request IS NULL THEN
  RAISE EXCEPTION USING ERRCODE='K0003',MESSAGE='Invalid enrollment context';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_issuer||':'||p_subject,0));
 IF EXISTS(SELECT 1 FROM kai.warehouse_revoked_subjects WHERE issuer=p_issuer AND subject=p_subject) THEN
  RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Warehouse access was permanently revoked';
 END IF;
 INSERT INTO kai.ai_principals(principal_id,issuer,subject,librechat_user_id)
  VALUES(gen_random_uuid(),p_issuer,p_actor,p_actor) ON CONFLICT DO NOTHING;
 INSERT INTO kai.ai_principals(principal_id,issuer,subject,librechat_user_id)
  VALUES(gen_random_uuid(),p_issuer,p_subject,p_subject) ON CONFLICT DO NOTHING;
 SELECT principal_id INTO actor_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_actor AND librechat_user_id=p_actor AND is_active;
 SELECT principal_id INTO target_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_subject AND librechat_user_id=p_subject AND is_active FOR UPDATE;
 IF actor_id IS NULL OR target_id IS NULL THEN
  RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Principal is unavailable';
 END IF;
 -- Failed first binding (no credential ever verified): the admin may switch to another ClickHouse username.
 SELECT * INTO b FROM kai.ch_bindings WHERE principal_id=target_id FOR UPDATE;
 IF b.binding_id IS NOT NULL AND b.ch_username<>p_username AND b.status='pending' AND b.cipher_blob IS NULL AND b.verified_at IS NULL THEN
  INSERT INTO kai.approved_ch_accounts(principal_id,ch_username) VALUES(target_id,p_username) ON CONFLICT DO NOTHING;
  SELECT * INTO mapping FROM kai.approved_ch_accounts WHERE ch_username=p_username AND released_at IS NULL FOR UPDATE;
  IF mapping.principal_id IS DISTINCT FROM target_id OR mapping.revoked_at IS NOT NULL THEN
   RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Mapping conflicts or was revoked';
  END IF;
  UPDATE kai.ch_bindings SET ch_username=p_username,service_target=p_service,revision=gen_random_uuid(),updated_at=clock_timestamp()
   WHERE principal_id=target_id;
  DELETE FROM kai.approved_ch_accounts a WHERE a.principal_id=target_id AND a.ch_username=b.ch_username
   AND NOT EXISTS(SELECT 1 FROM kai.access_approval_events e WHERE e.target_principal=target_id AND e.ch_username=b.ch_username);
 END IF;
 INSERT INTO kai.approved_ch_accounts(principal_id,ch_username) VALUES(target_id,p_username) ON CONFLICT DO NOTHING;
 SELECT * INTO mapping FROM kai.approved_ch_accounts WHERE ch_username=p_username AND released_at IS NULL FOR UPDATE;
 IF mapping.principal_id IS DISTINCT FROM target_id OR mapping.revoked_at IS NOT NULL THEN
  RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Mapping conflicts or was revoked';
 END IF;
 INSERT INTO kai.ch_bindings(binding_id,principal_id,ch_username,service_target)
  VALUES(gen_random_uuid(),target_id,p_username,p_service) ON CONFLICT(principal_id) DO NOTHING;
 SELECT * INTO b FROM kai.ch_bindings WHERE principal_id=target_id FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM kai.ai_principals WHERE principal_id=actor_id AND is_active)
 OR NOT EXISTS(SELECT 1 FROM kai.ai_principals WHERE principal_id=target_id AND is_active)
 OR b.ch_username<>p_username OR b.service_target IS DISTINCT FROM p_service OR b.status='revoked' THEN
  RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Binding target conflicts or was revoked';
 END IF;
 INSERT INTO kai.binding_enrollment_events(event_id,actor_principal,target_principal,action,ch_username,service_target)
  VALUES(p_request,actor_id,target_id,'prepare',p_username,p_service);
 RETURN kai.warehouse_status(p_actor,p_subject,p_username,p_issuer);
END $$;
REVOKE ALL ON FUNCTION kai.warehouse_prepare(TEXT,TEXT,TEXT,TEXT,TEXT,UUID) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kai.warehouse_release(p_actor TEXT,p_subject TEXT,p_issuer TEXT,p_request UUID) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor_id UUID; target_id UUID; b kai.ch_bindings%ROWTYPE;
BEGIN
 IF p_actor IS NULL OR p_actor !~ '^[0-9a-f]{24}$' OR p_subject IS NULL OR p_subject !~ '^[0-9a-f]{24}$'
 OR p_issuer IS NULL OR p_issuer !~ '[^[:space:]]' OR p_request IS NULL THEN
  RAISE EXCEPTION USING ERRCODE='K0003',MESSAGE='Invalid enrollment context';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_issuer||':'||p_subject,0));
 SELECT principal_id INTO actor_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_actor AND librechat_user_id=p_actor AND is_active;
 IF actor_id IS NULL THEN RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Principal is unavailable'; END IF;
 SELECT principal_id INTO target_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_subject AND librechat_user_id=p_subject;
 SELECT * INTO b FROM kai.ch_bindings WHERE principal_id=target_id FOR UPDATE;
 IF b.binding_id IS NULL OR b.status<>'revoked'
 OR NOT EXISTS(SELECT 1 FROM kai.warehouse_revoked_subjects WHERE issuer=p_issuer AND subject=p_subject) THEN
  RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Only a revoked binding can be released';
 END IF;
 UPDATE kai.approved_ch_accounts SET released_at=coalesce(released_at,clock_timestamp())
  WHERE principal_id=target_id AND ch_username=b.ch_username;
 INSERT INTO kai.binding_enrollment_events(event_id,actor_principal,target_principal,action,ch_username,service_target)
  VALUES(p_request,actor_id,target_id,'release',b.ch_username,b.service_target);
 RETURN kai.warehouse_status(p_actor,p_subject,b.ch_username,p_issuer);
END $$;
REVOKE ALL ON FUNCTION kai.warehouse_release(TEXT,TEXT,TEXT,UUID) FROM PUBLIC;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kai_enrollment_api') THEN
  GRANT EXECUTE ON FUNCTION kai.warehouse_release(TEXT,TEXT,TEXT,UUID) TO kai_enrollment_api;
 END IF;
END $$;
