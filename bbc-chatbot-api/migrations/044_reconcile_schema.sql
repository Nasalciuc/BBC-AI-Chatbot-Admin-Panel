-- 044: what production has and the repository never wrote down (PR-14).
--
-- The code reads and writes these, so they exist live; a database rebuilt
-- from migrations alone was missing them. Every statement is a no-op where
-- the object already exists, so this file is safe to run on production.
-- Types follow how the code uses each column; the baseline dump from the
-- live project (docs/schema/README.md) is the check on that.
--
-- APPLIED: nu (expected to change nothing live).

ALTER TABLE public.pipeline_runs ADD COLUMN IF NOT EXISTS tunnel text;
ALTER TABLE public.pipeline_runs ADD COLUMN IF NOT EXISTS had_fallback boolean NOT NULL DEFAULT false;

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS chats_served_today integer NOT NULL DEFAULT 0;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS chats_served_date date;

-- conversations.status gained 'needs_agent' (the silent reservation). Only
-- when the live CHECK does not accept it already is it rewritten; on
-- production this block changes nothing.
DO $$
DECLARE
    cname text;
    cdef  text;
BEGIN
    SELECT c.conname, pg_get_constraintdef(c.oid)
      INTO cname, cdef
      FROM pg_constraint c
     WHERE c.conrelid = 'public.conversations'::regclass
       AND c.contype = 'c'
       AND pg_get_constraintdef(c.oid) ~* '\mstatus\M'
     LIMIT 1;
    IF cname IS NOT NULL AND cdef NOT ILIKE '%needs_agent%' THEN
        EXECUTE format('ALTER TABLE public.conversations DROP CONSTRAINT %I', cname);
        ALTER TABLE public.conversations ADD CONSTRAINT conversations_status_check
            CHECK (status IN ('active', 'pending', 'closed', 'completed', 'needs_agent'));
    END IF;
END $$;
