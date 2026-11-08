-- 051: Tasks become follow-ups — what an agent promised a client in the chat,
-- what a client asked for, or a to-do someone added (the design canvas's
-- "Tasks · the new feature to build", 8 Nov 2026).
--
-- APPLIED: nu. The panel works without it: until it runs, the Tasks page
-- says the feature isn't set up yet and nothing is caught from the chats
-- (counted in /health as tasks.schema_missing). Sending a message is never
-- blocked by it.
--
-- Additive only, safe while the old code runs. The template's columns
-- (status, label, priority, due_date, notified_at) stay; nothing reads them
-- after this PR, and the table has 0 rows (8 Nov 2026).

ALTER TABLE public.tasks
  ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'todo',
  ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'added',
  ADD COLUMN IF NOT EXISTS conversation_id uuid NULL REFERENCES public.conversations(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS lead_id uuid NULL REFERENCES public.leads(id) ON DELETE SET NULL,
  -- The chat message the task was caught from (an agent's promise, a client's "call me").
  ADD COLUMN IF NOT EXISTS message_id uuid NULL REFERENCES public.messages(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS quote text NULL,
  ADD COLUMN IF NOT EXISTS due_at timestamptz NULL,
  -- How the due time was decided: said in the message, the default rule,
  -- "today" (end of the working day), picked by a person, or a day with no time.
  ADD COLUMN IF NOT EXISTS due_kind text NOT NULL DEFAULT 'picked',
  ADD COLUMN IF NOT EXISTS unowned_reason text NULL,
  ADD COLUMN IF NOT EXISTS unowned_from uuid NULL REFERENCES public.users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS unowned_at timestamptz NULL,
  ADD COLUMN IF NOT EXISTS done_at timestamptz NULL,
  ADD COLUMN IF NOT EXISTS done_by uuid NULL REFERENCES public.users(id) ON DELETE SET NULL,
  -- "Not a promise": a caught task someone said was not one.
  ADD COLUMN IF NOT EXISTS dismissed_at timestamptz NULL,
  ADD COLUMN IF NOT EXISTS dismissed_by uuid NULL REFERENCES public.users(id) ON DELETE SET NULL;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tasks_kind_check') THEN
    ALTER TABLE public.tasks ADD CONSTRAINT tasks_kind_check CHECK (kind IN ('call', 'email', 'todo'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tasks_source_check') THEN
    ALTER TABLE public.tasks ADD CONSTRAINT tasks_source_check
      CHECK (source IN ('caught', 'client_asked', 'added', 'dashboard'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tasks_due_kind_check') THEN
    ALTER TABLE public.tasks ADD CONSTRAINT tasks_due_kind_check
      CHECK (due_kind IN ('said', 'default', 'end_of_day', 'picked', 'day'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'tasks_unowned_reason_check') THEN
    ALTER TABLE public.tasks ADD CONSTRAINT tasks_unowned_reason_check
      CHECK (unowned_reason IS NULL OR unowned_reason IN ('ai_only', 'left_shift'));
  END IF;
END $$;

-- Open tasks by due time (the page, the reminders) and by person (the menu count).
CREATE INDEX IF NOT EXISTS idx_tasks_open_due ON public.tasks (due_at)
  WHERE done_at IS NULL AND dismissed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_open_assignee ON public.tasks (assignee_id)
  WHERE done_at IS NULL AND dismissed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_conversation ON public.tasks (conversation_id)
  WHERE conversation_id IS NOT NULL;
-- One task per chat message: catching the same message twice (a retry) makes nothing new.
CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_message ON public.tasks (message_id)
  WHERE message_id IS NOT NULL;

-- What happened to a task, for its History: made, reminder, due changed,
-- given to someone, Unowned, done, "Not a promise".
CREATE TABLE IF NOT EXISTS public.task_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id uuid NOT NULL REFERENCES public.tasks(id) ON DELETE CASCADE,
  kind text NOT NULL,
  actor_id uuid NULL REFERENCES public.users(id) ON DELETE SET NULL,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON public.task_events (task_id, at);

ALTER TABLE public.task_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS task_events_service_role_all ON public.task_events;
CREATE POLICY task_events_service_role_all
  ON public.task_events
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);

-- The rules an owner changes on Tasks → Rules. NULL = the defaults in
-- app/services/follow_ups.py (DEFAULT_RULES).
ALTER TABLE public.app_settings ADD COLUMN IF NOT EXISTS task_rules jsonb NULL;
