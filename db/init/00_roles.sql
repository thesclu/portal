-- Local development bootstrap. Idempotent. Run against the maintenance database
-- as a superuser (the Homebrew install trusts local connections).
--
-- Two roles, on purpose:
--   portal_owner  runs migrations and owns every table and function.
--   portal_app    is what the web server and the worker connect as. It gets
--                 only the privileges migration records_db.0002 grants it:
--                 no DELETE anywhere in records.*, and UPDATE only on the
--                 columns the data model calls mutable.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_owner') THEN
    CREATE ROLE portal_owner LOGIN CREATEDB;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    CREATE ROLE portal_app LOGIN NOBYPASSRLS;
  END IF;
END
$$;

-- Lets the owner (and therefore the test suite) SET ROLE portal_app to prove
-- that the app role is refused what it should be refused.
GRANT portal_app TO portal_owner;

SELECT 'CREATE DATABASE sclu_portal OWNER portal_owner'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'sclu_portal')\gexec

ALTER DATABASE sclu_portal SET search_path = core, records, public;
ALTER ROLE portal_app SET search_path = core, records, public;
ALTER ROLE portal_owner SET search_path = core, records, public;
