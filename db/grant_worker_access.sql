-- Schema-owner operation after catalog migration; this grants queue processing only.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
    IF (SELECT nspowner FROM pg_namespace WHERE nspname='kai') IS DISTINCT FROM current_user::regrole THEN
        RAISE EXCEPTION 'Run grants as the owner of schema kai';
    END IF;
    IF to_regclass('kai.ingestion_jobs') IS NULL OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kai_metadata_worker') THEN
        RAISE EXCEPTION 'Catalog and worker group prerequisites are required';
    END IF;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kai_metadata_worker'
              AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
       OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member='kai_metadata_worker'::regrole)
       OR has_schema_privilege('kai_metadata_worker','kai','CREATE')
       OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='kai' AND c.relkind IN ('r','v','m','f','p') AND (
                   (c.relname<>'ingestion_jobs' AND (has_table_privilege('kai_metadata_worker',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    OR has_any_column_privilege('kai_metadata_worker',c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
                   OR (c.relname='ingestion_jobs' AND has_table_privilege('kai_metadata_worker',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,SELECT WITH GRANT OPTION'))))
       OR EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid='kai.ingestion_jobs'::regclass AND a.attnum>0 AND NOT a.attisdropped AND (
                 has_column_privilege('kai_metadata_worker',a.attrelid,a.attname,'INSERT,REFERENCES,UPDATE WITH GRANT OPTION,SELECT WITH GRANT OPTION')
                 OR (a.attname NOT IN ('status','attempts','available_at','locked_until','last_error_code','updated_at')
                     AND has_column_privilege('kai_metadata_worker',a.attrelid,a.attname,'UPDATE'))))
       OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                 WHERE n.nspname='kai' AND has_function_privilege('kai_metadata_worker',p.oid,'EXECUTE'))
       OR EXISTS(SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass
                 AND refobjid='kai_metadata_worker'::regrole AND deptype='o') THEN
        RAISE EXCEPTION 'Worker role has excess grants or memberships; inspect before granting';
    END IF;
END $$;
REVOKE ALL ON kai.ingestion_jobs FROM kai_metadata_worker;
GRANT USAGE ON SCHEMA kai TO kai_metadata_worker;
GRANT SELECT ON kai.ingestion_jobs TO kai_metadata_worker;
GRANT UPDATE(status,attempts,available_at,locked_until,last_error_code,updated_at) ON kai.ingestion_jobs TO kai_metadata_worker;
COMMIT;
