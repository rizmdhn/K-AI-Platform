CREATE TABLE kai.access_approval_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL UNIQUE,
    actor_principal UUID NOT NULL REFERENCES kai.ai_principals(principal_id),
    target_principal UUID NOT NULL,
    ch_username TEXT NOT NULL,
    approval_role TEXT NOT NULL CHECK(approval_role IN ('platform_owner','data_owner')),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY(target_principal,ch_username) REFERENCES kai.approved_ch_accounts(principal_id,ch_username)
);
REVOKE ALL ON kai.access_approval_events FROM PUBLIC;

-- Narrow atomic operation; API role gets EXECUTE, never raw approval-table writes.
CREATE FUNCTION kai.record_account_approval(p_actor_subject TEXT,p_target_subject TEXT,p_username TEXT,
    p_category TEXT,p_expiry TIMESTAMPTZ,p_issuer TEXT,p_request UUID) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
    actor_id UUID;
    target_id UUID;
    mapping kai.approved_ch_accounts%ROWTYPE;
    existing_actor UUID;
    other_actor UUID;
    decision_at TIMESTAMPTZ;
BEGIN
    IF p_actor_subject IS NULL OR p_actor_subject !~ '^[0-9a-f]{24}$'
       OR p_target_subject IS NULL OR p_target_subject !~ '^[0-9a-f]{24}$'
       OR p_username IS NULL OR p_username !~ '[^[:space:]]' OR octet_length(p_username)>1024
       OR p_category IS NULL OR p_category NOT IN ('platform_owner','data_owner')
       OR p_issuer IS NULL OR p_issuer !~ '[^[:space:]]' OR p_request IS NULL
       OR (p_expiry IS NOT NULL AND (NOT isfinite(p_expiry) OR p_expiry<=clock_timestamp())) THEN
        RAISE EXCEPTION USING ERRCODE='K0003',MESSAGE='Invalid approval request';
    END IF;
    SELECT p.principal_id INTO actor_id FROM kai.ai_principals p
      JOIN kai.warehouse_approval_authorities a ON a.principal_id=p.principal_id
      WHERE p.issuer=p_issuer AND p.subject=p_actor_subject AND p.librechat_user_id=p_actor_subject
        AND p.is_active AND a.is_active AND a.approval_role=p_category;
    IF actor_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='K0001',MESSAGE='Warehouse approval authority is required';
    END IF;
    SELECT p.principal_id INTO target_id FROM kai.ai_principals p
      WHERE p.issuer=p_issuer AND p.subject=p_target_subject AND p.librechat_user_id=p_target_subject AND p.is_active;
    IF target_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Approval target is unavailable';
    END IF;
    INSERT INTO kai.approved_ch_accounts(principal_id,ch_username,expires_at)
      VALUES(target_id,p_username,p_expiry) ON CONFLICT DO NOTHING;
    SELECT a.* INTO mapping FROM kai.approved_ch_accounts a WHERE a.ch_username=p_username FOR UPDATE;
    IF mapping.principal_id IS DISTINCT FROM target_id OR mapping.expires_at IS DISTINCT FROM p_expiry
       OR mapping.revoked_at IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Approval target or scope conflicts';
    END IF;
    -- A lock wait may outlive an authority designation or target activation.
    IF NOT EXISTS(SELECT 1 FROM kai.ai_principals p JOIN kai.warehouse_approval_authorities a
      ON a.principal_id=p.principal_id WHERE p.principal_id=actor_id AND p.is_active
      AND a.approval_role=p_category AND a.is_active) THEN
        RAISE EXCEPTION USING ERRCODE='K0001',MESSAGE='Warehouse approval authority is required';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM kai.ai_principals WHERE principal_id=target_id AND is_active) THEN
        RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Approval target is unavailable';
    END IF;
    decision_at=clock_timestamp();
    IF mapping.expires_at<=decision_at THEN
        RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Approval scope expired while waiting';
    END IF;
    IF p_category='platform_owner' THEN
        existing_actor=mapping.owner_approved_by; other_actor=mapping.data_owner_approved_by;
    ELSE
        existing_actor=mapping.data_owner_approved_by; other_actor=mapping.owner_approved_by;
    END IF;
    IF other_actor=actor_id OR (existing_actor IS NOT NULL AND existing_actor<>actor_id) THEN
        RAISE EXCEPTION USING ERRCODE='K0002',MESSAGE='Separate decisions cannot replace existing consent';
    END IF;
    IF existing_actor=actor_id THEN
        RETURN jsonb_build_object('already_recorded',true,'both_approvals_recorded',
            mapping.owner_approved_by IS NOT NULL AND mapping.data_owner_approved_by IS NOT NULL);
    END IF;
    IF p_category='platform_owner' THEN
        UPDATE kai.approved_ch_accounts SET owner_approved_by=actor_id,owner_approved_at=decision_at
          WHERE principal_id=target_id AND ch_username=p_username RETURNING * INTO mapping;
    ELSE
        UPDATE kai.approved_ch_accounts SET data_owner_approved_by=actor_id,data_owner_approved_at=decision_at
          WHERE principal_id=target_id AND ch_username=p_username RETURNING * INTO mapping;
    END IF;
    INSERT INTO kai.access_approval_events(request_id,actor_principal,target_principal,ch_username,approval_role,expires_at,created_at)
      VALUES(p_request,actor_id,target_id,p_username,p_category,p_expiry,decision_at);
    RETURN jsonb_build_object('already_recorded',false,'both_approvals_recorded',
        mapping.owner_approved_by IS NOT NULL AND mapping.data_owner_approved_by IS NOT NULL);
END $$;
REVOKE ALL ON FUNCTION kai.record_account_approval(TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,TEXT,UUID) FROM PUBLIC;
