-- Apply through db/migrate.sql. Model price per 1M tokens for the admin dashboard cost estimate (no secret).
ALTER TABLE kai.integration_settings DROP CONSTRAINT integration_settings_name_check,
    ADD CONSTRAINT integration_settings_name_check CHECK (name IN ('openmetadata', 'batch_model', 'limits', 'smtp', 'pricing'));
