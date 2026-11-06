-- Approval history, including old expiry, remains available but does not gate admin-managed mappings.
CREATE OR REPLACE VIEW kai.active_ch_bindings WITH (security_barrier=true) AS
SELECT b.binding_id,b.principal_id,p.issuer,p.subject,p.librechat_user_id,
 b.ch_username,b.cipher_blob,b.key_version,b.verified_at,b.updated_at,b.service_target
FROM kai.ch_bindings b
JOIN kai.ai_principals p ON p.principal_id=b.principal_id AND p.is_active
JOIN kai.approved_ch_accounts a ON a.principal_id=b.principal_id AND a.ch_username=b.ch_username
WHERE b.status='active' AND b.verified_at<=statement_timestamp()
 AND a.revoked_at IS NULL;

CREATE OR REPLACE FUNCTION kai.warehouse_ready(p_target UUID,p_username TEXT) RETURNS BOOLEAN
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM kai.approved_ch_accounts a
 JOIN kai.ai_principals p ON p.principal_id=a.principal_id AND p.is_active
 WHERE a.principal_id=p_target AND a.ch_username=p_username AND a.revoked_at IS NULL
 );
$$;
REVOKE ALL ON FUNCTION kai.warehouse_ready(UUID,TEXT) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kai.warehouse_status(p_actor TEXT,p_subject TEXT,p_username TEXT,p_issuer TEXT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE target_id UUID; b kai.ch_bindings%ROWTYPE; ready BOOLEAN; state TEXT;
BEGIN
 IF p_actor IS NULL OR p_actor !~ '^[0-9a-f]{24}$' OR p_subject IS NULL OR p_subject !~ '^[0-9a-f]{24}$'
 OR p_issuer IS NULL OR p_issuer !~ '[^[:space:]]' THEN
  RAISE EXCEPTION USING ERRCODE='K0003',MESSAGE='Invalid enrollment context';
 END IF;
 IF EXISTS(SELECT 1 FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_actor AND librechat_user_id=p_actor AND NOT is_active) THEN
  RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Principal is unavailable';
 END IF;
 SELECT principal_id INTO target_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_subject AND librechat_user_id=p_subject;
 SELECT * INTO b FROM kai.ch_bindings WHERE principal_id=target_id;
 ready=kai.warehouse_ready(target_id,coalesce(b.ch_username,p_username));
 state=CASE WHEN target_id IS NULL OR b.binding_id IS NULL THEN 'not_prepared'
  WHEN b.status='revoked' OR NOT ready THEN 'revoked'
  WHEN b.status='active' AND b.cipher_blob IS NOT NULL AND b.key_version IS NOT NULL
   AND b.verified_at<=clock_timestamp() THEN 'active' ELSE 'ready' END;
 RETURN jsonb_build_object('state',state,'principal_id',target_id,'binding_id',b.binding_id,'revision',b.revision,
  'service_target',b.service_target,'ch_username',coalesce(b.ch_username,p_username),
  'credentials_saved',b.cipher_blob IS NOT NULL);
END $$;
REVOKE ALL ON FUNCTION kai.warehouse_status(TEXT,TEXT,TEXT,TEXT) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kai.warehouse_save(p_actor TEXT,p_subject TEXT,p_username TEXT,p_service TEXT,p_issuer TEXT,
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
  RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Binding or mapping changed';
 END IF;
 UPDATE kai.ch_bindings SET cipher_blob=p_cipher,key_version=p_key,status='active',verified_at=clock_timestamp(),
  rotated_at=CASE WHEN b.cipher_blob IS NOT NULL THEN clock_timestamp() ELSE NULL END,
  revision=gen_random_uuid(),updated_at=clock_timestamp() WHERE principal_id=target_id;
 INSERT INTO kai.binding_enrollment_events(event_id,actor_principal,target_principal,action,ch_username,service_target)
  VALUES(p_request,actor_id,target_id,'save',p_username,p_service);
 RETURN kai.warehouse_status(p_actor,p_subject,p_username,p_issuer);
END $$;
REVOKE ALL ON FUNCTION kai.warehouse_save(TEXT,TEXT,TEXT,TEXT,TEXT,UUID,UUID,BYTEA,INTEGER,UUID) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kai.warehouse_revoke(p_actor TEXT,p_subject TEXT,p_issuer TEXT,p_request UUID) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor_id UUID; target_id UUID; b kai.ch_bindings%ROWTYPE;
BEGIN
 IF p_actor IS NULL OR p_actor !~ '^[0-9a-f]{24}$' OR p_subject IS NULL OR p_subject !~ '^[0-9a-f]{24}$'
 OR p_issuer IS NULL OR p_issuer !~ '[^[:space:]]' OR p_request IS NULL THEN
  RAISE EXCEPTION USING ERRCODE='K0003',MESSAGE='Invalid enrollment context';
 END IF;
 -- The enrollment service has already verified this native admin before invoking SQL.
 INSERT INTO kai.ai_principals(principal_id,issuer,subject,librechat_user_id)
  VALUES(gen_random_uuid(),p_issuer,p_actor,p_actor) ON CONFLICT DO NOTHING;
 SELECT principal_id INTO actor_id FROM kai.ai_principals WHERE issuer=p_issuer AND subject=p_actor AND librechat_user_id=p_actor AND is_active;
 IF actor_id IS NULL THEN
  RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Principal is unavailable';
 END IF;
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
