-- Apply through db/migrate.sql. Outgoing mail (SMTP) settings for activation / password reset links; password encrypted like the other secrets.
ALTER TABLE kai.integration_settings DROP CONSTRAINT integration_settings_name_check,
    ADD CONSTRAINT integration_settings_name_check CHECK (name IN ('openmetadata', 'batch_model', 'limits', 'smtp'));
