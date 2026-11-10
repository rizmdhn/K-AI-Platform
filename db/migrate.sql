-- Run with psql -X -v ON_ERROR_STOP=1 -f db/migrate.sql as the migration owner.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '30s';
SELECT pg_advisory_xact_lock(181832325, 1);

CREATE SCHEMA IF NOT EXISTS kai AUTHORIZATION CURRENT_USER;
DO $$
BEGIN
    IF (SELECT nspowner FROM pg_namespace WHERE nspname = 'kai') <> current_user::regrole THEN
        RAISE EXCEPTION 'Run migrations as the owner of schema kai';
    END IF;
    IF to_regclass('kai.ai_principals') IS NULL THEN
        RAISE EXCEPTION 'Identity prerequisite missing: kai.ai_principals (P2-02)';
    END IF;
END
$$;

REVOKE ALL ON SCHEMA kai FROM PUBLIC;
CREATE TABLE IF NOT EXISTS kai.schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
REVOKE ALL ON kai.schema_migrations FROM PUBLIC;

SELECT NOT EXISTS (
    SELECT 1 FROM kai.schema_migrations WHERE version = '001_catalog'
) AS apply_catalog \gset
\if :apply_catalog
    \ir migrations/001_catalog.sql
    INSERT INTO kai.schema_migrations (version) VALUES ('001_catalog');
\endif

SELECT NOT EXISTS (
    SELECT 1 FROM kai.schema_migrations WHERE version = '006_integration_settings'
) AS apply_integration_settings \gset
\if :apply_integration_settings
    \ir migrations/006_integration_settings.sql
    INSERT INTO kai.schema_migrations (version) VALUES ('006_integration_settings');
\endif

SELECT NOT EXISTS (
    SELECT 1 FROM kai.schema_migrations WHERE version = '007_more_settings'
) AS apply_more_settings \gset
\if :apply_more_settings
    \ir migrations/007_more_settings.sql
    INSERT INTO kai.schema_migrations (version) VALUES ('007_more_settings');
\endif
COMMIT;
