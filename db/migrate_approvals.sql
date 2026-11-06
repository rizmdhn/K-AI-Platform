\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='30s';
SELECT pg_advisory_xact_lock(181832325,1);
DO $$ BEGIN
    IF (SELECT nspowner FROM pg_namespace WHERE nspname='kai') IS DISTINCT FROM current_user::regrole
       OR to_regclass('kai.warehouse_approval_authorities') IS NULL THEN
        RAISE EXCEPTION 'Apply identity migration first, as the owner of schema kai';
    END IF;
END $$;
SELECT NOT EXISTS (SELECT 1 FROM kai.schema_migrations WHERE version='002_approval_events') AS apply_approvals \gset
\if :apply_approvals
    \ir migrations/002_approval_events.sql
    INSERT INTO kai.schema_migrations(version) VALUES('002_approval_events');
\endif
COMMIT;
