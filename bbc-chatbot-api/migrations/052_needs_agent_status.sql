-- 052: conversations.status accepts 'needs_agent' — the client asked for a person.
--
-- APPLIED: nu. The owner runs it in working hours, with someone watching the
-- queue, AFTER the code of this PR is deployed (that code is safe without
-- it: every needs_agent write keeps failing, as it has all along, but now it
-- is counted in /health/detail → needs_agent.write_failed, code 22001).
--
-- Why: the code has written status = 'needs_agent' since the V2 queue, and
-- production has refused every one of those writes — 'needs_agent' is 11
-- characters, the column is varchar(10), and the CHECK leaves it out. So a
-- client who asked for a person and found nobody free never entered the
-- line: the queue, the "Asked for a person" badge, the attention e-mail and
-- the redistribution at /open never saw them. 0 rows hold it (2026-11-09).
--
-- Live before (checked 2026-11-09): status varchar(10); CHECK (active,
-- pending, closed, completed); uq_conv_active_visitor on visitor_id WHERE
-- status = 'active'. No view depends on the column.
--
-- What it changes:
--   1. status becomes varchar(20). Widening a varchar rewrites nothing.
--   2. The status CHECK (whatever its name) is replaced by one that also
--      accepts 'needs_agent'.
--   3. "One open conversation per visitor" covers needs_agent too: a visitor
--      waiting for a person must not get a second chat (and a twin would make
--      the claim needs_agent → active fail on this same index). The index is
--      rebuilt with the same name — the code matches the name on a 23505.
--      With 0 needs_agent rows, the new index holds exactly the old one's
--      rows, so it cannot fail on a duplicate.
-- One transaction: either all three apply or none. lock_timeout: if a long
-- transaction holds the table, this gives up after 5 s instead of queueing
-- every chat write behind it — run it again a minute later.
--
-- After it runs, both must hold:
--   SELECT character_maximum_length FROM information_schema.columns
--    WHERE table_schema = 'public' AND table_name = 'conversations'
--      AND column_name = 'status';                                  -- 20
--   SELECT pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'public.conversations'::regclass
--      AND conname = 'conversations_status_check';                  -- has needs_agent
-- and in /health/detail, needs_agent.write_failed stops growing.
--
-- To undo (the code then goes back to counting refused writes, as before):
--   BEGIN;
--   UPDATE public.conversations SET status = 'active' WHERE status = 'needs_agent';
--   ALTER TABLE public.conversations DROP CONSTRAINT conversations_status_check;
--   ALTER TABLE public.conversations ADD CONSTRAINT conversations_status_check
--     CHECK (status IN ('active', 'pending', 'closed', 'completed'));
--   DROP INDEX public.uq_conv_active_visitor;
--   CREATE UNIQUE INDEX uq_conv_active_visitor ON public.conversations (visitor_id)
--     WHERE status = 'active' AND visitor_id IS NOT NULL;
--   COMMIT;

BEGIN;

SET LOCAL lock_timeout = '5s';

ALTER TABLE public.conversations ALTER COLUMN status TYPE varchar(20);

-- Every CHECK on the status column alone (by column number, not by text:
-- returning_client_status has a CHECK of its own that must stay).
DO $$
DECLARE
    cname text;
BEGIN
    FOR cname IN
        SELECT c.conname
          FROM pg_constraint c
         WHERE c.conrelid = 'public.conversations'::regclass
           AND c.contype = 'c'
           AND c.conkey = ARRAY[(
                 SELECT a.attnum FROM pg_attribute a
                  WHERE a.attrelid = 'public.conversations'::regclass
                    AND a.attname = 'status')]
    LOOP
        EXECUTE format('ALTER TABLE public.conversations DROP CONSTRAINT %I', cname);
    END LOOP;
END $$;

ALTER TABLE public.conversations ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('active', 'pending', 'closed', 'completed', 'needs_agent'));

DROP INDEX IF EXISTS public.uq_conv_active_visitor;
CREATE UNIQUE INDEX uq_conv_active_visitor
    ON public.conversations (visitor_id)
 WHERE status IN ('active', 'needs_agent') AND visitor_id IS NOT NULL;

COMMIT;
