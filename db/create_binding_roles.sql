-- DBA operation, separate from schema-owner migrations. Creates no login/password.
\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(181832325, 2);
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='kai_mcp') THEN
        CREATE ROLE kai_mcp NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='kai_mcp'
               AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
       OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member='kai_mcp'::regrole)
       OR EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass
                    AND refobjid='kai_mcp'::regrole AND deptype='o') THEN
        RAISE EXCEPTION 'Existing kai_mcp role is privileged or has other memberships; inspect before granting';
    END IF;
END
$$;
COMMIT;
