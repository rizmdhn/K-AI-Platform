-- DBA only, in the metadata database after owner grants. Never sets a password or enables login.
\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(181832325,2);
DO $$
DECLARE profile record; runtime_oid oid; group_oid oid; allowed oid[];
BEGIN
 IF to_regnamespace('kai') IS NULL THEN
  RAISE EXCEPTION 'Run metadata migrations and owner grants first';
 END IF;
 FOR profile IN SELECT * FROM (VALUES
  ('kai_binding_runtime','kai_mcp'),
  ('kai_enrollment_runtime','kai_enrollment_api')) AS profiles(runtime_name,group_name)
 LOOP
  SELECT oid INTO group_oid FROM pg_roles WHERE rolname=profile.group_name;
  IF group_oid IS NULL OR EXISTS(SELECT 1 FROM pg_roles WHERE oid=group_oid AND
   (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
   OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member=group_oid)
   OR EXISTS(SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass AND refobjid=group_oid AND deptype='o') THEN
   RAISE EXCEPTION 'Permission group % is missing or privileged; inspect DBA and owner grant steps',profile.group_name;
  END IF;
  allowed=CASE profile.group_name
   WHEN 'kai_mcp' THEN ARRAY[]::oid[]
   ELSE ARRAY[to_regprocedure('kai.warehouse_status(text,text,text,text)')::oid,
    to_regprocedure('kai.warehouse_prepare(text,text,text,text,text,uuid)')::oid,
    to_regprocedure('kai.warehouse_save(text,text,text,text,text,uuid,uuid,bytea,integer,uuid)')::oid,
    to_regprocedure('kai.warehouse_revoke(text,text,text,uuid)')::oid] END;
  IF array_position(allowed,NULL) IS NOT NULL
   OR NOT has_schema_privilege(group_oid,'kai','USAGE')
   OR has_schema_privilege(group_oid,'kai','CREATE')
   OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='kai' AND c.relkind IN ('r','v','m','f','p') AND (
     ((profile.group_name<>'kai_mcp' OR c.relname<>'active_ch_bindings') AND
      (has_table_privilege(group_oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
       OR has_any_column_privilege(group_oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))
     OR (profile.group_name='kai_mcp' AND c.relname='active_ch_bindings' AND
      (NOT has_table_privilege(group_oid,c.oid,'SELECT')
       OR has_table_privilege(group_oid,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,SELECT WITH GRANT OPTION')
       OR has_any_column_privilege(group_oid,c.oid,'INSERT,UPDATE,REFERENCES,SELECT WITH GRANT OPTION')))))
   OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='kai' AND ((NOT p.oid=ANY(allowed) AND has_function_privilege(group_oid,p.oid,'EXECUTE'))
     OR has_function_privilege(group_oid,p.oid,'EXECUTE WITH GRANT OPTION')))
   OR EXISTS(SELECT 1 FROM unnest(allowed) f(oid) WHERE NOT has_function_privilege(group_oid,f.oid,'EXECUTE')) THEN
   RAISE EXCEPTION 'Permission group % lacks expected grants or has excess application access',profile.group_name;
  END IF;
  SELECT oid INTO runtime_oid FROM pg_roles WHERE rolname=profile.runtime_name;
  IF runtime_oid IS NULL THEN
   EXECUTE format('CREATE ROLE %I NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',profile.runtime_name);
   SELECT oid INTO runtime_oid FROM pg_roles WHERE rolname=profile.runtime_name;
  END IF;
  IF EXISTS(SELECT 1 FROM pg_roles WHERE oid=runtime_oid AND
    (NOT rolinherit OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
   OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member=runtime_oid AND (roleid<>group_oid OR admin_option))
   OR EXISTS(SELECT 1 FROM pg_shdepend WHERE refclassid='pg_authid'::regclass AND refobjid=runtime_oid AND deptype='o')
   -- Runtime permissions belong exclusively to the group, including column/default ACLs.
   OR EXISTS(SELECT 1 FROM pg_namespace n, LATERAL aclexplode(n.nspacl) a WHERE n.nspname='kai' AND a.grantee=runtime_oid)
   OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
      LATERAL aclexplode(c.relacl) a WHERE n.nspname='kai' AND a.grantee=runtime_oid)
   OR EXISTS(SELECT 1 FROM pg_attribute att JOIN pg_class c ON c.oid=att.attrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace, LATERAL aclexplode(att.attacl) a
      WHERE n.nspname='kai' AND a.grantee=runtime_oid)
   OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace,
      LATERAL aclexplode(p.proacl) a WHERE n.nspname='kai' AND a.grantee=runtime_oid)
   OR EXISTS(SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace,
      LATERAL aclexplode(t.typacl) a WHERE n.nspname='kai' AND a.grantee=runtime_oid)
   OR EXISTS(SELECT 1 FROM pg_default_acl d, LATERAL aclexplode(d.defaclacl) a
      WHERE (d.defaclnamespace=0 OR d.defaclnamespace='kai'::regnamespace) AND a.grantee=runtime_oid) THEN
   RAISE EXCEPTION 'Runtime role % has excess privileges, memberships, ownership or direct application grants; inspect manually',profile.runtime_name;
  END IF;
  EXECUTE format('GRANT %I TO %I',profile.group_name,profile.runtime_name);
 END LOOP;
END $$;
COMMIT;
