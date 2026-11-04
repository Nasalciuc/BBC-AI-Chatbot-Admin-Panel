-- Migration 013: Add QA auditor and dev roles to users.role CHECK constraint
DO $$
DECLARE
    constraint_name text;
    constraint_def  text;
BEGIN
    SELECT conname, pg_get_constraintdef(c.oid) INTO constraint_name, constraint_def
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
    IF constraint_name IS NOT NULL AND constraint_def ILIKE '%''qa''%' AND constraint_def ILIKE '%''dev''%' THEN
        RETURN;
    END IF;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE users DROP CONSTRAINT %I', constraint_name);
    END IF;

    ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('owner', 'admin', 'dev', 'sales', 'support', 'supervisor', 'qa'));
END $$;
