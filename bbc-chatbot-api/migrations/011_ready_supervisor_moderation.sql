-- Migration 008: Ready status, supervisor role, and moderation fields
-- Safe to run multiple times where possible.

-- 1) Users: add readiness toggle column
ALTER TABLE users
ADD COLUMN IF NOT EXISTS is_ready boolean NOT NULL DEFAULT true;

-- 2) Conversations: add moderation flags
ALTER TABLE conversations
ADD COLUMN IF NOT EXISTS has_flagged_content boolean NOT NULL DEFAULT false;

ALTER TABLE conversations
ADD COLUMN IF NOT EXISTS flagged_reason text;

-- 3) Extend users.role CHECK to include supervisor
DO $$
DECLARE
    constraint_name text;
    constraint_def  text;
BEGIN
    SELECT conname, pg_get_constraintdef(c.oid)
    INTO constraint_name, constraint_def
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE t.relname = 'users'
      AND n.nspname = current_schema()
      AND c.contype = 'c'
      -- PR-14 repair: Postgres renders an IN list as `role = ANY (ARRAY[...])`,
      -- so the old ILIKE '%role IN%' never matched and the ADD below collided
      -- with the existing constraint on a clean replay. Match the name, or
      -- either rendering of the role list. A varchar column renders as
      -- `(role)::text = ANY (...)`: the gap may hold the cast (review fix).
      AND (c.conname = 'users_role_check'
           OR pg_get_constraintdef(c.oid) ~* '\mrole\M[^=]{0,30}(=\s*ANY|\s+IN\s*\()')
    LIMIT 1;

    -- Review fix: on a database that already accepts these roles (production
    -- does, and has project_manager from 021 too), re-running this file must
    -- change nothing — rewriting the list would drop the roles added later.
    IF constraint_name IS NOT NULL AND constraint_def ILIKE '%''supervisor''%' THEN
        RETURN;
    END IF;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE users DROP CONSTRAINT %I', constraint_name);
    END IF;

    ALTER TABLE users
    ADD CONSTRAINT users_role_check
    CHECK (role IN ('owner', 'admin', 'sales', 'support', 'supervisor'));
END $$;
