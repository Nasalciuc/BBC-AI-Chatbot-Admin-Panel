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
BEGIN
    SELECT conname
    INTO constraint_name
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE t.relname = 'users'
      AND n.nspname = current_schema()
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) ILIKE '%role IN%';

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE users DROP CONSTRAINT %I', constraint_name);
    END IF;

    ALTER TABLE users
    ADD CONSTRAINT users_role_check
    CHECK (role IN ('owner', 'admin', 'sales', 'support', 'supervisor'));
END $$;
