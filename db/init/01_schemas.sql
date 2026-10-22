-- Run inside sclu_portal. Idempotent. The Django pre_migrate hook does the
-- same thing, so a freshly created test database gets its schemas too.
CREATE SCHEMA IF NOT EXISTS core AUTHORIZATION portal_owner;
CREATE SCHEMA IF NOT EXISTS records AUTHORIZATION portal_owner;
GRANT USAGE ON SCHEMA core, records TO portal_app;
