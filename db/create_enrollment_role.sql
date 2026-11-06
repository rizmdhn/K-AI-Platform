\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(181832325,2);
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kai_enrollment_api') THEN
  CREATE ROLE kai_enrollment_api NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kai_enrollment_api' AND
  (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
 OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member='kai_enrollment_api'::regrole)
 OR EXISTS(SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass AND refobjid='kai_enrollment_api'::regrole AND deptype='o') THEN
  RAISE EXCEPTION 'Existing enrollment role is privileged; inspect before granting';
 END IF;
END $$;
COMMIT;
