-- 036: runtime switches in the database, so a rollback is one UPDATE, not a deploy.
--
-- app/flags.py reads app_settings.flags (the singleton row, id = 1) every 30 s.
-- Keys are flag names in upper case, values are JSON scalars:
--   UPDATE app_settings SET flags = flags || '{"PAX_TOOL_FIELDS": true}' WHERE id = 1;
-- A missing key falls back to the environment variable of the same name, then
-- to the code's default, which is always today's behaviour.
--
-- APPLIED: nu. The code treats a missing column as "no flags in the DB".

ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS flags jsonb NOT NULL DEFAULT '{}'::jsonb;

-- The row the reader expects. 003_seed inserts it; this keeps a fresh database honest.
INSERT INTO app_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
