-- Separate DBA operation; creates no login/password and is not run by the worker.
\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(181832325, 4);
DO $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kai_metadata_worker') THEN
        CREATE ROLE kai_metadata_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kai_metadata_worker'
              AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
       OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member='kai_metadata_worker'::regrole)
       OR EXISTS(SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass
                 AND refobjid='kai_metadata_worker'::regrole AND deptype='o') THEN
        RAISE EXCEPTION 'Existing worker group is privileged; inspect before granting';
    END IF;
END $$;
COMMIT;
