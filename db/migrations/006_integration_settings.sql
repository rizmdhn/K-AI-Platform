-- Apply through db/migrate.sql. Integration settings edited in the Admin Panel (instead of .env).
-- The secret (e.g. the OpenMetadata bot token) is AES-256-GCM encrypted with the binding keyring.
CREATE TABLE kai.integration_settings (
    name TEXT PRIMARY KEY CHECK (name IN ('openmetadata')),
    config JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(config) = 'object'),
    secret BYTEA CHECK (secret IS NULL OR octet_length(secret) >= 29),
    key_version INTEGER CHECK (key_version > 0),
    updated_by TEXT CHECK (updated_by ~ '^[0-9a-f]{24}$'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((secret IS NULL) = (key_version IS NULL))
);
REVOKE ALL ON kai.integration_settings FROM PUBLIC;
