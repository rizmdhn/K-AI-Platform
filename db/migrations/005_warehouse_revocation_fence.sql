-- Serialize prepare and revoke even when a binding does not exist yet.
CREATE TABLE kai.warehouse_revoked_subjects (
 issuer TEXT NOT NULL, subject TEXT NOT NULL, revoked_by TEXT NOT NULL,
 request_id UUID NOT NULL, revoked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(issuer,subject)
);
REVOKE ALL ON kai.warehouse_revoked_subjects FROM PUBLIC;

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
 INSERT INTO kai.approved_ch_accounts(principal_id,ch_username) VALUES(target_id,p_username) ON CONFLICT DO NOTHING;
 SELECT * INTO mapping FROM kai.approved_ch_accounts WHERE ch_username=p_username FOR UPDATE;
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

CREATE OR REPLACE FUNCTION kai.warehouse_revoke(p_actor TEXT,p_subject TEXT,p_issuer TEXT,p_request UUID) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor_id UUID; target_id UUID; b kai.ch_bindings%ROWTYPE;
BEGIN
 IF p_actor IS NULL OR p_actor !~ '^[0-9a-f]{24}$' OR p_subject IS NULL OR p_subject !~ '^[0-9a-f]{24}$'
 OR p_issuer IS NULL OR p_issuer !~ '[^[:space:]]' OR p_request IS NULL THEN
  RAISE EXCEPTION USING ERRCODE='K0003',MESSAGE='Invalid enrollment context';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_issuer||':'||p_subject,0));
 INSERT INTO kai.ai_principals(principal_id,issuer,subject,librechat_user_id)
  VALUES(gen_random_uuid(),p_issuer,p_actor,p_actor) ON CONFLICT DO NOTHING;
 SELECT principal_id INTO actor_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_actor AND librechat_user_id=p_actor AND is_active;
 IF actor_id IS NULL THEN RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Principal is unavailable'; END IF;
 INSERT INTO kai.warehouse_revoked_subjects(issuer,subject,revoked_by,request_id)
  VALUES(p_issuer,p_subject,p_actor,p_request) ON CONFLICT DO NOTHING;
 SELECT principal_id INTO target_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_subject AND librechat_user_id=p_subject;
 SELECT * INTO b FROM kai.ch_bindings WHERE principal_id=target_id FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM kai.ai_principals WHERE principal_id=actor_id AND is_active) THEN
  RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Principal is unavailable';
 END IF;
 IF b.binding_id IS NULL THEN RETURN kai.warehouse_status(p_actor,p_subject,NULL,p_issuer); END IF;
 UPDATE kai.ch_bindings SET status='revoked',cipher_blob=NULL,key_version=NULL,revision=gen_random_uuid(),updated_at=clock_timestamp()
  WHERE principal_id=target_id;
 UPDATE kai.approved_ch_accounts SET revoked_at=coalesce(revoked_at,clock_timestamp())
  WHERE principal_id=target_id AND ch_username=b.ch_username;
 INSERT INTO kai.binding_enrollment_events(event_id,actor_principal,target_principal,action,ch_username,service_target)
  VALUES(p_request,actor_id,target_id,'revoke',b.ch_username,b.service_target);
 RETURN kai.warehouse_status(p_actor,p_subject,b.ch_username,p_issuer);
END $$;
REVOKE ALL ON FUNCTION kai.warehouse_revoke(TEXT,TEXT,TEXT,UUID) FROM PUBLIC;
