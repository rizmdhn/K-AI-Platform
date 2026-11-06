CREATE TABLE kai.ai_principals (
    principal_id UUID PRIMARY KEY,
    issuer TEXT NOT NULL CHECK (issuer ~ '[^[:space:]]'),
    subject TEXT NOT NULL CHECK (subject ~ '[^[:space:]]'),
    librechat_user_id TEXT UNIQUE CHECK (librechat_user_id ~ '[^[:space:]]'),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (issuer, subject)
);

-- Explicit designation; platform ADMIN does not implicitly hold either authority.
CREATE TABLE kai.warehouse_approval_authorities (
    principal_id UUID NOT NULL REFERENCES kai.ai_principals(principal_id),
    approval_role TEXT NOT NULL CHECK (approval_role IN ('platform_owner','data_owner')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, approval_role)
);

CREATE TABLE kai.approved_ch_accounts (
    principal_id UUID NOT NULL REFERENCES kai.ai_principals(principal_id),
    ch_username TEXT NOT NULL UNIQUE CHECK (ch_username ~ '[^[:space:]]'),
    owner_approved_by UUID REFERENCES kai.ai_principals(principal_id),
    owner_approved_at TIMESTAMPTZ,
    data_owner_approved_by UUID REFERENCES kai.ai_principals(principal_id),
    data_owner_approved_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, ch_username),
    CHECK ((owner_approved_by IS NULL) = (owner_approved_at IS NULL)),
    CHECK ((data_owner_approved_by IS NULL) = (data_owner_approved_at IS NULL)),
    CHECK (owner_approved_by IS NULL OR data_owner_approved_by IS NULL
           OR owner_approved_by <> data_owner_approved_by)
);

CREATE TABLE kai.ch_bindings (
    binding_id UUID PRIMARY KEY,
    principal_id UUID NOT NULL UNIQUE REFERENCES kai.ai_principals(principal_id),
    ch_username TEXT NOT NULL,
    cipher_blob BYTEA,
    key_version INTEGER CHECK (key_version > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','revoked')),
    verified_at TIMESTAMPTZ,
    rotated_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (principal_id, ch_username) REFERENCES kai.approved_ch_accounts(principal_id, ch_username),
    CHECK ((cipher_blob IS NULL) = (key_version IS NULL)),
    CHECK (cipher_blob IS NULL OR octet_length(cipher_blob) >= 29),
    CHECK (status <> 'active' OR (cipher_blob IS NOT NULL AND verified_at IS NOT NULL)),
    CHECK (status <> 'revoked' OR (cipher_blob IS NULL AND key_version IS NULL))
);

CREATE FUNCTION kai.protect_binding_identity() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF TG_TABLE_NAME='ai_principals' THEN
        IF ROW(NEW.principal_id,NEW.issuer,NEW.subject,NEW.librechat_user_id)
           IS DISTINCT FROM ROW(OLD.principal_id,OLD.issuer,OLD.subject,OLD.librechat_user_id) THEN
            RAISE EXCEPTION 'Principal identity is immutable; create a new principal and obtain fresh approval';
        END IF;
    ELSIF TG_TABLE_NAME='approved_ch_accounts' THEN
        IF ROW(NEW.principal_id,NEW.ch_username) IS DISTINCT FROM ROW(OLD.principal_id,OLD.ch_username) THEN
            RAISE EXCEPTION 'Approval target is immutable; create a new mapping and obtain fresh approval';
        END IF;
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION kai.protect_binding_identity() FROM PUBLIC;
CREATE TRIGGER immutable_principal_identity BEFORE UPDATE ON kai.ai_principals
FOR EACH ROW EXECUTE FUNCTION kai.protect_binding_identity();
CREATE TRIGGER immutable_approval_target BEFORE UPDATE ON kai.approved_ch_accounts
FOR EACH ROW EXECUTE FUNCTION kai.protect_binding_identity();

-- Only this view is granted to MCP. Re-evaluate for every tool call, without a binding cache.
CREATE VIEW kai.active_ch_bindings WITH (security_barrier=true) AS
SELECT b.binding_id, b.principal_id, p.issuer, p.subject, p.librechat_user_id,
       b.ch_username, b.cipher_blob, b.key_version, b.verified_at, b.updated_at
FROM kai.ch_bindings b
JOIN kai.ai_principals p ON p.principal_id=b.principal_id AND p.is_active
JOIN kai.approved_ch_accounts a ON a.principal_id=b.principal_id AND a.ch_username=b.ch_username
JOIN kai.ai_principals owner_actor ON owner_actor.principal_id=a.owner_approved_by AND owner_actor.is_active
JOIN kai.ai_principals data_actor ON data_actor.principal_id=a.data_owner_approved_by AND data_actor.is_active
JOIN kai.warehouse_approval_authorities owner_authority
  ON owner_authority.principal_id=a.owner_approved_by AND owner_authority.approval_role='platform_owner'
     AND owner_authority.is_active
JOIN kai.warehouse_approval_authorities data_authority
  ON data_authority.principal_id=a.data_owner_approved_by AND data_authority.approval_role='data_owner'
     AND data_authority.is_active
WHERE b.status='active' AND b.verified_at <= statement_timestamp()
  AND a.owner_approved_at <= statement_timestamp() AND a.data_owner_approved_at <= statement_timestamp()
  AND a.revoked_at IS NULL AND (a.expires_at IS NULL OR a.expires_at > statement_timestamp());

REVOKE ALL ON kai.ai_principals, kai.warehouse_approval_authorities,
    kai.approved_ch_accounts, kai.ch_bindings, kai.active_ch_bindings FROM PUBLIC;
