\set ON_ERROR_STOP on
BEGIN;
DO $$ DECLARE allowed_oid oid; BEGIN
    IF (SELECT nspowner FROM pg_namespace WHERE nspname='kai') IS DISTINCT FROM current_user::regrole THEN
        RAISE EXCEPTION 'Run approval grants as schema owner';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kai_approval_api') THEN
        RAISE EXCEPTION 'Create the approval permission group first';
    END IF;
    allowed_oid=to_regprocedure('kai.record_account_approval(text,text,text,text,timestamptz,text,uuid)');
    IF allowed_oid IS NULL OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kai_approval_api'
      AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
      OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member='kai_approval_api'::regrole)
      OR EXISTS(SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass AND refobjid='kai_approval_api'::regrole AND deptype='o')
      OR has_schema_privilege('kai_approval_api','kai','CREATE')
      OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='kai' AND c.relkind IN ('r','v','m','f','p') AND
          (has_table_privilege('kai_approval_api',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
           OR has_any_column_privilege('kai_approval_api',c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
      OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='kai' AND ((p.oid<>allowed_oid AND has_function_privilege('kai_approval_api',p.oid,'EXECUTE'))
          OR has_function_privilege('kai_approval_api',p.oid,'EXECUTE WITH GRANT OPTION'))) THEN
        RAISE EXCEPTION 'Approval role has excess grants or operation is unavailable';
    END IF;
END $$;
GRANT USAGE ON SCHEMA kai TO kai_approval_api;
GRANT EXECUTE ON FUNCTION kai.record_account_approval(TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,TEXT,UUID) TO kai_approval_api;
COMMIT;
