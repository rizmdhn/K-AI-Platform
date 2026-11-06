ALTER TABLE kai.ch_bindings ADD COLUMN service_target TEXT
    CHECK(service_target ~ '^https://[a-z0-9.-]+:[0-9]{1,5}$' AND octet_length(service_target)<=512);
ALTER TABLE kai.ch_bindings ADD COLUMN revision UUID NOT NULL DEFAULT gen_random_uuid();
CREATE TABLE kai.binding_enrollment_events (
    event_id UUID PRIMARY KEY,
    actor_principal UUID NOT NULL REFERENCES kai.ai_principals(principal_id),
    target_principal UUID NOT NULL REFERENCES kai.ai_principals(principal_id),
    action TEXT NOT NULL CHECK(action IN ('prepare','save','revoke')),
    ch_username TEXT NOT NULL,
    -- NULL records an unknown legacy target during revocation; never invent a target.
    service_target TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON kai.binding_enrollment_events FROM PUBLIC;

-- Append a column to preserve existing view grants and the original eligibility rules.
CREATE OR REPLACE VIEW kai.active_ch_bindings WITH (security_barrier=true) AS
SELECT b.binding_id,b.principal_id,p.issuer,p.subject,p.librechat_user_id,
 b.ch_username,b.cipher_blob,b.key_version,b.verified_at,b.updated_at,b.service_target
FROM kai.ch_bindings b
JOIN kai.ai_principals p ON p.principal_id=b.principal_id AND p.is_active
JOIN kai.approved_ch_accounts a ON a.principal_id=b.principal_id AND a.ch_username=b.ch_username
JOIN kai.ai_principals o ON o.principal_id=a.owner_approved_by AND o.is_active
JOIN kai.ai_principals d ON d.principal_id=a.data_owner_approved_by AND d.is_active
JOIN kai.warehouse_approval_authorities oa ON oa.principal_id=o.principal_id AND oa.approval_role='platform_owner' AND oa.is_active
JOIN kai.warehouse_approval_authorities da ON da.principal_id=d.principal_id AND da.approval_role='data_owner' AND da.is_active
WHERE b.status='active' AND b.verified_at<=statement_timestamp()
 AND a.owner_approved_at<=statement_timestamp() AND a.data_owner_approved_at<=statement_timestamp()
 AND a.revoked_at IS NULL AND (a.expires_at IS NULL OR a.expires_at>statement_timestamp());

CREATE FUNCTION kai.warehouse_ready(p_target UUID,p_username TEXT) RETURNS BOOLEAN
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM kai.approved_ch_accounts a
 JOIN kai.ai_principals p ON p.principal_id=a.principal_id AND p.is_active
 JOIN kai.ai_principals o ON o.principal_id=a.owner_approved_by AND o.is_active
 JOIN kai.ai_principals d ON d.principal_id=a.data_owner_approved_by AND d.is_active
 JOIN kai.warehouse_approval_authorities oa ON oa.principal_id=o.principal_id AND oa.approval_role='platform_owner' AND oa.is_active
 JOIN kai.warehouse_approval_authorities da ON da.principal_id=d.principal_id AND da.approval_role='data_owner' AND da.is_active
 WHERE a.principal_id=p_target AND a.ch_username=p_username
 AND a.owner_approved_at<=clock_timestamp() AND a.data_owner_approved_at<=clock_timestamp()
 AND a.owner_approved_by<>a.data_owner_approved_by AND a.revoked_at IS NULL
 AND (a.expires_at IS NULL OR a.expires_at>clock_timestamp()));
$$;
REVOKE ALL ON FUNCTION kai.warehouse_ready(UUID,TEXT) FROM PUBLIC;

CREATE FUNCTION kai.warehouse_status(p_actor TEXT,p_subject TEXT,p_username TEXT,p_issuer TEXT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE target_id UUID; actor_id UUID; b kai.ch_bindings%ROWTYPE; a kai.approved_ch_accounts%ROWTYPE; ready BOOLEAN; state TEXT;
BEGIN
 IF p_actor IS NULL OR p_actor !~ '^[0-9a-f]{24}$' OR p_subject IS NULL OR p_subject !~ '^[0-9a-f]{24}$'
 OR p_issuer IS NULL OR p_issuer !~ '[^[:space:]]' THEN
  RAISE EXCEPTION USING ERRCODE='K0003',MESSAGE='Invalid enrollment context';
 END IF;
 SELECT principal_id INTO target_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_subject AND librechat_user_id=p_subject;
 SELECT principal_id INTO actor_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_actor AND librechat_user_id=p_actor AND is_active;
 SELECT * INTO b FROM kai.ch_bindings WHERE principal_id=target_id;
 SELECT * INTO a FROM kai.approved_ch_accounts WHERE principal_id=target_id AND ch_username=coalesce(b.ch_username,p_username);
 ready=kai.warehouse_ready(target_id,a.ch_username);
 state=CASE WHEN target_id IS NULL OR b.binding_id IS NULL THEN 'not_prepared'
  WHEN b.status='revoked' OR a.revoked_at IS NOT NULL THEN 'revoked'
  WHEN NOT ready THEN 'pending_approval' WHEN b.status='active' THEN 'active' ELSE 'ready' END;
 RETURN jsonb_build_object('state',state,'principal_id',target_id,'binding_id',b.binding_id,'revision',b.revision,
  'service_target',b.service_target,'ch_username',coalesce(b.ch_username,p_username),
  'owner_approved',a.owner_approved_by IS NOT NULL,'data_owner_approved',a.data_owner_approved_by IS NOT NULL,
  'can_approve_owner',EXISTS(SELECT 1 FROM kai.warehouse_approval_authorities WHERE principal_id=actor_id AND approval_role='platform_owner' AND is_active)
     AND a.data_owner_approved_by IS DISTINCT FROM actor_id,
  'can_approve_data',EXISTS(SELECT 1 FROM kai.warehouse_approval_authorities WHERE principal_id=actor_id AND approval_role='data_owner' AND is_active)
     AND a.owner_approved_by IS DISTINCT FROM actor_id,
  'credentials_saved',b.cipher_blob IS NOT NULL,'expires_at',a.expires_at);
END $$;
REVOKE ALL ON FUNCTION kai.warehouse_status(TEXT,TEXT,TEXT,TEXT) FROM PUBLIC;

CREATE FUNCTION kai.warehouse_prepare(p_actor TEXT,p_subject TEXT,p_username TEXT,p_service TEXT,p_issuer TEXT,p_request UUID) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE target_id UUID; actor_id UUID; mapping kai.approved_ch_accounts%ROWTYPE; b kai.ch_bindings%ROWTYPE;
BEGIN
 IF p_actor IS NULL OR p_actor !~ '^[0-9a-f]{24}$' OR p_subject IS NULL OR p_subject !~ '^[0-9a-f]{24}$'
 OR p_username IS NULL OR p_username !~ '[^[:space:]]' OR octet_length(p_username)>1024
 OR p_service IS NULL OR p_service !~ '^https://[a-z0-9.-]+:[0-9]{1,5}$' OR octet_length(p_service)>512
 OR p_issuer IS NULL OR p_issuer !~ '[^[:space:]]' OR p_request IS NULL THEN
  RAISE EXCEPTION USING ERRCODE='K0003',MESSAGE='Invalid enrollment context';
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

CREATE FUNCTION kai.warehouse_save(p_actor TEXT,p_subject TEXT,p_username TEXT,p_service TEXT,p_issuer TEXT,
 p_binding UUID,p_revision UUID,p_cipher BYTEA,p_key INTEGER,p_request UUID) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor_id UUID; target_id UUID; b kai.ch_bindings%ROWTYPE;
BEGIN
 IF p_cipher IS NULL OR octet_length(p_cipher) NOT BETWEEN 29 AND 4124 OR p_key IS NULL OR p_key<1 OR p_request IS NULL THEN
  RAISE EXCEPTION USING ERRCODE='K0003',MESSAGE='Invalid encrypted enrollment';
 END IF;
 SELECT principal_id INTO actor_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_actor AND librechat_user_id=p_actor AND is_active;
 SELECT principal_id INTO target_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_subject AND librechat_user_id=p_subject AND is_active;
 SELECT * INTO b FROM kai.ch_bindings WHERE principal_id=target_id FOR UPDATE;
 IF actor_id IS NULL OR b.binding_id IS DISTINCT FROM p_binding OR b.revision IS DISTINCT FROM p_revision
 OR NOT EXISTS(SELECT 1 FROM kai.ai_principals WHERE principal_id=actor_id AND is_active)
 OR b.status='revoked' OR b.ch_username IS DISTINCT FROM p_username OR b.service_target IS DISTINCT FROM p_service
 OR NOT kai.warehouse_ready(target_id,p_username) THEN
  RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Binding or approval changed';
 END IF;
 UPDATE kai.ch_bindings SET cipher_blob=p_cipher,key_version=p_key,status='active',verified_at=clock_timestamp(),
  rotated_at=CASE WHEN b.cipher_blob IS NOT NULL THEN clock_timestamp() ELSE NULL END,
  revision=gen_random_uuid(),updated_at=clock_timestamp() WHERE principal_id=target_id;
 INSERT INTO kai.binding_enrollment_events(event_id,actor_principal,target_principal,action,ch_username,service_target)
  VALUES(p_request,actor_id,target_id,'save',p_username,p_service);
 RETURN kai.warehouse_status(p_actor,p_subject,p_username,p_issuer);
END $$;
REVOKE ALL ON FUNCTION kai.warehouse_save(TEXT,TEXT,TEXT,TEXT,TEXT,UUID,UUID,BYTEA,INTEGER,UUID) FROM PUBLIC;

CREATE FUNCTION kai.warehouse_revoke(p_actor TEXT,p_subject TEXT,p_issuer TEXT,p_request UUID) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor_id UUID; target_id UUID; b kai.ch_bindings%ROWTYPE;
BEGIN
 SELECT principal_id INTO actor_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_actor AND librechat_user_id=p_actor AND is_active;
 SELECT principal_id INTO target_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_subject AND librechat_user_id=p_subject;
 SELECT * INTO b FROM kai.ch_bindings WHERE principal_id=target_id FOR UPDATE;
 IF actor_id IS NULL OR b.binding_id IS NULL OR p_request IS NULL
 OR NOT EXISTS(SELECT 1 FROM kai.ai_principals WHERE principal_id=actor_id AND is_active) THEN
  RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Binding is unavailable';
 END IF;
 UPDATE kai.ch_bindings SET status='revoked',cipher_blob=NULL,key_version=NULL,revision=gen_random_uuid(),updated_at=clock_timestamp()
  WHERE principal_id=target_id;
 UPDATE kai.approved_ch_accounts SET revoked_at=coalesce(revoked_at,clock_timestamp())
  WHERE principal_id=target_id AND ch_username=b.ch_username;
 INSERT INTO kai.binding_enrollment_events(event_id,actor_principal,target_principal,action,ch_username,service_target)
  VALUES(p_request,actor_id,target_id,'revoke',b.ch_username,b.service_target);
 RETURN kai.warehouse_status(p_actor,p_subject,b.ch_username,p_issuer);
END $$;
REVOKE ALL ON FUNCTION kai.warehouse_revoke(TEXT,TEXT,TEXT,UUID) FROM PUBLIC;
