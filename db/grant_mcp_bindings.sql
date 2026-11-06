-- Run as the schema/view owner after migrate_identity.sql and the DBA role step.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
    IF (SELECT nspowner FROM pg_namespace WHERE nspname='kai') IS DISTINCT FROM current_user::regrole THEN
        RAISE EXCEPTION 'Run binding grants as the owner of schema kai';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='kai_mcp') THEN
        RAISE EXCEPTION 'Run create_binding_roles.sql as the DBA first';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='kai_mcp'
               AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
       OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member='kai_mcp'::regrole) THEN
        RAISE EXCEPTION 'Existing kai_mcp role is privileged or has other memberships';
    END IF;
    IF has_schema_privilege('kai_mcp','kai','CREATE') OR EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='kai' AND c.relkind IN ('r','v','m','f','p') AND (
            (c.relname <> 'active_ch_bindings' AND (
                has_table_privilege('kai_mcp',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                OR has_any_column_privilege('kai_mcp',c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
            OR (c.relname='active_ch_bindings' AND (
                has_table_privilege('kai_mcp',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,SELECT WITH GRANT OPTION')
                OR has_any_column_privilege('kai_mcp',c.oid,'INSERT,UPDATE,REFERENCES,SELECT WITH GRANT OPTION')))
        )
    ) OR EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='kai' AND has_function_privilege('kai_mcp',p.oid,'EXECUTE')
    ) OR EXISTS (SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass
                  AND refobjid='kai_mcp'::regrole AND deptype='o') THEN
        RAISE EXCEPTION 'Existing kai_mcp role has excess application grants or owns objects; inspect before granting';
    END IF;
END
$$;
GRANT USAGE ON SCHEMA kai TO kai_mcp;
GRANT SELECT ON kai.active_ch_bindings TO kai_mcp;
COMMIT;
