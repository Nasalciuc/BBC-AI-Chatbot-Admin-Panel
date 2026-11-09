-- 044: what production has and the repository never wrote down (PR-14).
--
-- The code reads and writes these, so they exist live; a database rebuilt
-- from migrations alone was missing them. Each statement copies the live
-- definition (read from the live schema on 2026-11-09) and is a no-op where
-- it already exists, so on production this file changes nothing.
--
-- APPLIED: nu (changes nothing live; checked against the live schema
-- on 2026-11-09).

ALTER TABLE public.pipeline_runs ADD COLUMN IF NOT EXISTS tunnel varchar(10);
ALTER TABLE public.pipeline_runs ADD COLUMN IF NOT EXISTS had_fallback boolean DEFAULT false;

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS chats_served_today integer NOT NULL DEFAULT 0;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS chats_served_date date DEFAULT CURRENT_DATE;

-- conversations.status: production accepts 'completed' (001 wrote only
-- active, pending and closed) and stays varchar(10). It does NOT accept
-- 'needs_agent', although the code writes it: live, every such write is
-- rejected (too long for varchar(10), and not in the CHECK). Accepting it
-- changes what production does (the queue, reserved chats, the attention
-- emails start seeing it), so it gets its own migration and PR, not this
-- one. Here the CHECK is rewritten only where it lacks 'completed', to
-- production's exact list.
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
    IF cname IS NOT NULL AND cdef NOT ILIKE '%completed%' THEN
        EXECUTE format('ALTER TABLE public.conversations DROP CONSTRAINT %I', cname);
        ALTER TABLE public.conversations ADD CONSTRAINT conversations_status_check
            CHECK (status IN ('active', 'pending', 'closed', 'completed'));
    END IF;
END $$;
