\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='30s';
SELECT pg_advisory_xact_lock(181832325,1);
DO $$ BEGIN
 IF (SELECT nspowner FROM pg_namespace WHERE nspname='kai') IS DISTINCT FROM current_user::regrole
 OR NOT EXISTS(SELECT 1 FROM kai.schema_migrations WHERE version='004_admin_managed_warehouse') THEN
  RAISE EXCEPTION 'Apply migration004 as schema owner first';
 END IF;
END $$;
SELECT NOT EXISTS(SELECT 1 FROM kai.schema_migrations WHERE version='005_warehouse_revocation_fence') AS apply_revocation_fence \gset
\if :apply_revocation_fence
 \ir migrations/005_warehouse_revocation_fence.sql
 INSERT INTO kai.schema_migrations(version) VALUES('005_warehouse_revocation_fence');
\endif
COMMIT;
