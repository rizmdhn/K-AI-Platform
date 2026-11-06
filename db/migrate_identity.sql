-- Custom application database only; run separately before the catalog migration.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '30s';
SELECT pg_advisory_xact_lock(181832325, 1);
CREATE SCHEMA IF NOT EXISTS kai AUTHORIZATION CURRENT_USER;
DO $$
BEGIN
    IF (SELECT nspowner FROM pg_namespace WHERE nspname='kai') <> current_user::regrole THEN
        RAISE EXCEPTION 'Run identity migrations as the owner of schema kai';
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
    SELECT 1 FROM kai.schema_migrations WHERE version='000_identity'
) AS apply_identity \gset
\if :apply_identity
    \ir migrations/000_identity.sql
    INSERT INTO kai.schema_migrations (version) VALUES ('000_identity');
\endif
COMMIT;
