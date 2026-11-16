-- 053: The departure task — a client who leaves after giving a contact, with
-- nobody on the chat, becomes a task for the free agent (app/services/departure.py,
-- ADR 09 of the proactive-chat debate, 15 Nov 2026).
--
-- APPLIED: nu. The job is off at merge (DEPARTURE_TASKS_ENABLED in
-- app_settings.flags). Turn it on only after this runs: before it, the
-- database refuses the task's source ('departure') and its Unowned reason
-- ('no_ready_agent'), and the job counts every refusal in /health
-- (tasks.departure_failures). Nothing else depends on it.
--
-- Live before (checked 2026-11-15): tasks_source_check (caught, client_asked,
-- added, dashboard); tasks_unowned_reason_check (NULL, ai_only, left_shift).
-- 2 rows, both source 'added'.
--
-- What it changes: the two checks accept one more value each, and a partial
-- unique index keeps "at most one departure task per chat, ever" in the
-- database: two runs of the job that overlap (the old and the new process
-- during a deploy each run the scheduler, as on 5 Nov) can both pass the
-- job's own reads, and the second insert is refused (23505). No data is
-- rewritten; no row has source 'departure' before this runs, so the index
-- builds on what is there. One transaction; lock_timeout: if a long
-- transaction holds the table, this gives up after 5 s instead of queueing
-- every write behind it.

BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE public.tasks DROP CONSTRAINT IF EXISTS tasks_source_check;
ALTER TABLE public.tasks ADD CONSTRAINT tasks_source_check
  CHECK (source IN ('caught', 'client_asked', 'added', 'dashboard', 'departure'));

ALTER TABLE public.tasks DROP CONSTRAINT IF EXISTS tasks_unowned_reason_check;
ALTER TABLE public.tasks ADD CONSTRAINT tasks_unowned_reason_check
  CHECK (unowned_reason IS NULL OR unowned_reason IN ('ai_only', 'left_shift', 'no_ready_agent'));

CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_departure ON public.tasks (conversation_id)
  WHERE source = 'departure';

COMMIT;
