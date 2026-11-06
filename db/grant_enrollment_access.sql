\set ON_ERROR_STOP on
BEGIN;
DO $$ DECLARE allowed oid[]; BEGIN
 IF (SELECT nspowner FROM pg_namespace WHERE nspname='kai') IS DISTINCT FROM current_user::regrole
 OR NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kai_enrollment_api') THEN
  RAISE EXCEPTION 'Run enrollment grants as schema owner after role creation';
 END IF;
 allowed=ARRAY[
  to_regprocedure('kai.warehouse_status(text,text,text,text)')::oid,
  to_regprocedure('kai.warehouse_prepare(text,text,text,text,text,uuid)')::oid,
  to_regprocedure('kai.warehouse_save(text,text,text,text,text,uuid,uuid,bytea,integer,uuid)')::oid,
  to_regprocedure('kai.warehouse_revoke(text,text,text,uuid)')::oid];
 IF array_position(allowed,NULL) IS NOT NULL
 OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kai_enrollment_api' AND
   (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
 OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member='kai_enrollment_api'::regrole)
 OR EXISTS(SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass AND refobjid='kai_enrollment_api'::regrole AND deptype='o')
 OR has_schema_privilege('kai_enrollment_api','kai','CREATE')
 OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='kai'
   AND c.relkind IN ('r','v','m','f','p') AND (has_table_privilege('kai_enrollment_api',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
   OR has_any_column_privilege('kai_enrollment_api',c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
 OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='kai'
   AND ((NOT p.oid=ANY(allowed) AND has_function_privilege('kai_enrollment_api',p.oid,'EXECUTE'))
   OR has_function_privilege('kai_enrollment_api',p.oid,'EXECUTE WITH GRANT OPTION'))) THEN
  RAISE EXCEPTION 'Enrollment role has excess grants or operations are unavailable';
 END IF;
END $$;
GRANT USAGE ON SCHEMA kai TO kai_enrollment_api;
GRANT EXECUTE ON FUNCTION kai.warehouse_status(TEXT,TEXT,TEXT,TEXT),
 kai.warehouse_prepare(TEXT,TEXT,TEXT,TEXT,TEXT,UUID),
 kai.warehouse_save(TEXT,TEXT,TEXT,TEXT,TEXT,UUID,UUID,BYTEA,INTEGER,UUID),
 kai.warehouse_revoke(TEXT,TEXT,TEXT,UUID) TO kai_enrollment_api;
COMMIT;
