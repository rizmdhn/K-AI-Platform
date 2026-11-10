-- Apply through db/migrate.sql. Admin Panel sections beyond OpenMetadata: batch model (API key encrypted like
-- the bot token) and query/log limits. Same table, one row per section.
ALTER TABLE kai.integration_settings DROP CONSTRAINT integration_settings_name_check,
    ADD CONSTRAINT integration_settings_name_check CHECK (name IN ('openmetadata', 'batch_model', 'limits'));
