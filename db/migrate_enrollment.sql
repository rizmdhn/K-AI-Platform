\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='30s';
SELECT pg_advisory_xact_lock(181832325,1);
DO $$ BEGIN
 IF (SELECT nspowner FROM pg_namespace WHERE nspname='kai') IS DISTINCT FROM current_user::regrole
 OR to_regclass('kai.access_approval_events') IS NULL THEN
  RAISE EXCEPTION 'Apply identity and approval migrations as schema owner first';
 END IF;
END $$;
SELECT NOT EXISTS(SELECT 1 FROM kai.schema_migrations WHERE version='003_warehouse_enrollment') AS apply_enrollment \gset
\if :apply_enrollment
 \ir migrations/003_warehouse_enrollment.sql
 INSERT INTO kai.schema_migrations(version) VALUES('003_warehouse_enrollment');
\endif
COMMIT;
