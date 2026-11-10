-- Schema-owner operation after db/migrate.sql: catalog tables only, never bindings or approvals.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
    IF (SELECT nspowner FROM pg_namespace WHERE nspname='kai') IS DISTINCT FROM current_user::regrole THEN
        RAISE EXCEPTION 'Run catalog grants as the owner of schema kai';
    END IF;
    IF to_regclass('kai.catalog_assets') IS NULL OR to_regclass('kai.integration_settings') IS NULL OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='kai_catalog') THEN
        RAISE EXCEPTION 'Run db/migrate.sql and create_catalog_role.sql first';
    END IF;
END
$$;
GRANT USAGE ON SCHEMA kai TO kai_catalog;
-- The worker records which OpenMetadata suggestions it already sent (one row per table and schema version).
GRANT SELECT, INSERT, UPDATE ON kai.catalog_assets TO kai_catalog;
-- Admin Panel integration settings (OpenMetadata): read by the MCP and worker, written via the MCP's internal route.
GRANT SELECT, INSERT, UPDATE ON kai.integration_settings TO kai_catalog;
COMMIT;
