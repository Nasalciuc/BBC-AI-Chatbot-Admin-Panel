-- ============================================================
-- 034 — QUEUED_AT: when a conversation entered the shared queue.
-- FILE ONLY — apply manually, then register:
--   INSERT INTO supabase_migrations.schema_migrations (version, name)
--   VALUES ('20260819000034', '034_queued_at') ON CONFLICT DO NOTHING;
-- ============================================================
ALTER TABLE public.conversations ADD COLUMN IF NOT EXISTS queued_at timestamptz;
CREATE INDEX IF NOT EXISTS idx_conversations_queued
  ON public.conversations (queued_at) WHERE assigned_agent_id IS NULL;
COMMENT ON COLUMN public.conversations.queued_at IS
  'Entry time into the shared queue. Written CONDITIONALLY (only when NULL) by '
  'the first-contact path in chat.py, and UNCONDITIONALLY by _release_from_agent '
  'for queue reasons (agent_offline / released_by_agent / supervisor), because a '
  'release starts a new life in the line. Set to NULL by the claim gate — the '
  'waiting age is captured into claim_won BEFORE the NULL. The five terminal '
  'closes never touch it. Drives the queue age, the 60s silent reservation and '
  'the >2min alert.';
