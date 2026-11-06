-- Throwaway fixture mirroring Docs/user_matrix.md (roles, grants, analyst profile) with synthetic data.
-- Never run against a real cluster. Used by test_e2e_user_matrix.py.
CREATE DATABASE raw; CREATE DATABASE bronze; CREATE DATABASE silver; CREATE DATABASE gold;
CREATE TABLE raw.events (id UInt32, payload String) ENGINE = MergeTree ORDER BY id;
INSERT INTO raw.events VALUES (1, 'raw-1'), (2, 'raw-2');
CREATE TABLE bronze.events (id UInt32, payload String) ENGINE = MergeTree ORDER BY id;
CREATE TABLE silver.rute_ref (rute String, kota String) ENGINE = MergeTree ORDER BY rute;
INSERT INTO silver.rute_ref VALUES ('CGK-DPS', 'Denpasar');
CREATE TABLE silver.internal (id UInt32) ENGINE = MergeTree ORDER BY id;
CREATE TABLE gold.penjualan (region String, amount UInt32) ENGINE = MergeTree ORDER BY region;
INSERT INTO gold.penjualan VALUES ('ID', 100), ('SG', 50);
CREATE TABLE gold.penumpang_detail (id UInt32, rute String, tanggal Date, nama String) ENGINE = MergeTree ORDER BY id;
INSERT INTO gold.penumpang_detail VALUES (1, 'CGK-DPS', '2026-01-02', 'Penumpang Fiktif');

-- Verbatim from Docs/user_matrix.md (infra_dba trimmed to the SELECT grants kAI could ever use).
CREATE SETTINGS PROFILE IF NOT EXISTS sp_analyst_readonly
SETTINGS readonly = 2, max_execution_time = 300 CONST, max_memory_usage = 10000000000 CONST;
CREATE ROLE data_engineer;
GRANT SELECT, INSERT, ALTER, CREATE TABLE, CREATE VIEW, DROP TABLE, DROP VIEW ON raw.*    TO data_engineer;
GRANT SELECT, INSERT, ALTER, CREATE TABLE, CREATE VIEW, DROP TABLE, DROP VIEW ON bronze.* TO data_engineer;
GRANT SELECT, INSERT, ALTER, CREATE TABLE, CREATE VIEW, DROP TABLE, DROP VIEW ON silver.* TO data_engineer;
GRANT SELECT, INSERT, ALTER, CREATE TABLE, CREATE VIEW, DROP TABLE, DROP VIEW ON gold.*   TO data_engineer;
CREATE ROLE IF NOT EXISTS data_analyst SETTINGS PROFILE 'sp_analyst_readonly';
GRANT SELECT ON gold.* TO data_analyst;
REVOKE SELECT ON gold.penumpang_detail FROM data_analyst;
CREATE ROLE data_steward;
GRANT SELECT ON raw.* TO data_steward; GRANT SELECT ON bronze.* TO data_steward;
GRANT SELECT ON silver.* TO data_steward; GRANT SELECT ON gold.* TO data_steward;
CREATE ROLE pipeline_service;
GRANT SELECT, INSERT, ALTER ON raw.* TO pipeline_service;
GRANT SELECT, INSERT, ALTER ON bronze.* TO pipeline_service;
GRANT SELECT ON silver.* TO pipeline_service;
CREATE ROLE bi_service;
GRANT SELECT ON gold.* TO bi_service;
GRANT SELECT ON silver.rute_ref TO bi_service;
REVOKE SELECT ON gold.penumpang_detail FROM bi_service;
GRANT SELECT(id, rute, tanggal) ON gold.penumpang_detail TO bi_service;
CREATE ROLE infra_dba;
GRANT SELECT ON system.query_log TO infra_dba;
GRANT SELECT ON system.processes TO infra_dba;
GRANT SELECT ON system.tables TO infra_dba;
GRANT KILL QUERY ON *.* TO infra_dba;

CREATE USER u_engineer IDENTIFIED BY 'engineer-pw' DEFAULT ROLE data_engineer; GRANT data_engineer TO u_engineer;
CREATE USER u_analyst IDENTIFIED BY 'analyst-pw' DEFAULT ROLE data_analyst; GRANT data_analyst TO u_analyst;
CREATE USER u_steward IDENTIFIED BY 'steward-pw' DEFAULT ROLE data_steward; GRANT data_steward TO u_steward;
CREATE USER u_pipeline IDENTIFIED BY 'pipeline-pw' DEFAULT ROLE pipeline_service; GRANT pipeline_service TO u_pipeline;
CREATE USER u_bi IDENTIFIED BY 'bi-pw' DEFAULT ROLE bi_service; GRANT bi_service TO u_bi;
CREATE USER u_dba IDENTIFIED BY 'dba-pw' DEFAULT ROLE infra_dba; GRANT infra_dba TO u_dba;
