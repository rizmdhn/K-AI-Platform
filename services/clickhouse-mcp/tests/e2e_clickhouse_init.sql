-- Throwaway ClickHouse fixture for test_e2e_per_user.py. Never run against a real cluster.
CREATE DATABASE sales;
CREATE TABLE sales.orders (region String, amount UInt32) ENGINE = MergeTree ORDER BY region;
INSERT INTO sales.orders VALUES ('ID', 100), ('ID', 200), ('SG', 50);
CREATE TABLE sales.secret (note String) ENGINE = MergeTree ORDER BY note;
INSERT INTO sales.secret VALUES ('alice only');

CREATE USER alice IDENTIFIED BY 'alice-pw';
CREATE USER bob IDENTIFIED BY 'bob-pw';
CREATE USER nogrant IDENTIFIED BY 'nogrant-pw';

GRANT SELECT, INSERT ON sales.orders TO alice;
GRANT SELECT ON sales.secret TO alice;
GRANT SELECT ON sales.orders TO bob;

CREATE ROW POLICY id_only ON sales.orders FOR SELECT USING region = 'ID' TO alice;
CREATE ROW POLICY sg_only ON sales.orders FOR SELECT USING region = 'SG' TO bob;

-- Docs/user_matrix.md style: a DBA profile with readonly=2 and CONST limits (analyst), and an
-- account the DBA pinned to readonly=0, which kAI must refuse rather than run writable.
CREATE SETTINGS PROFILE sp_analyst_readonly SETTINGS readonly = 2, max_execution_time = 300 CONST, max_memory_usage = 10000000000 CONST;
CREATE ROLE data_analyst SETTINGS PROFILE 'sp_analyst_readonly';
GRANT SELECT ON sales.orders TO data_analyst;
CREATE USER carol IDENTIFIED BY 'carol-pw' DEFAULT ROLE data_analyst;
GRANT data_analyst TO carol;
CREATE SETTINGS PROFILE sp_pinned_writable SETTINGS readonly = 0 CONST;
CREATE USER dave IDENTIFIED BY 'dave-pw' SETTINGS PROFILE 'sp_pinned_writable';
GRANT SELECT ON sales.orders TO dave;
