-- Migration 013: Add QA auditor and dev roles to users.role CHECK constraint
DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE t.relname = 'users'
      AND n.nspname = current_schema()
      AND c.contype = 'c'
      -- PR-14 repair: Postgres renders an IN list as `role = ANY (ARRAY[...])`,
      -- so the old ILIKE '%role IN%' never matched and the ADD below collided
      -- with the existing constraint on a clean replay. Match the name, or
      -- either rendering of the role list.
      AND (c.conname = 'users_role_check'
           OR pg_get_constraintdef(c.oid) ~* '\mrole\M[^a-z_]*(=\s*ANY|\s+IN\s*\()')
    LIMIT 1;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE users DROP CONSTRAINT %I', constraint_name);
    END IF;

    ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('owner', 'admin', 'dev', 'sales', 'support', 'supervisor', 'qa'));
END $$;
