-- DBA operation: permission group for the metadata catalog worker. Creates no login/password.
\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(181832325, 2);
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='kai_catalog') THEN
        CREATE ROLE kai_catalog NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='kai_catalog_runtime') THEN
        CREATE ROLE kai_catalog_runtime NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('kai_catalog','kai_catalog_runtime')
               AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
       OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname='kai_catalog' AND rolcanlogin)
       OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member='kai_catalog'::regrole)
       OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member='kai_catalog_runtime'::regrole
                  AND (roleid<>'kai_catalog'::regrole OR admin_option)) THEN
        RAISE EXCEPTION 'Existing catalog roles are privileged or have other memberships; inspect before granting';
    END IF;
END
$$;
GRANT kai_catalog TO kai_catalog_runtime;
COMMIT;
