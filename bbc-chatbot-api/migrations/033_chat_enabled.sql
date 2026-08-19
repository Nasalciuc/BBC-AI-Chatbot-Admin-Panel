-- ============================================================
-- 033 — CHAT_ENABLED: management-controlled right to be in the shared chat
-- system. FILE ONLY — apply manually, then register:
--   INSERT INTO supabase_migrations.schema_migrations (version, name)
--   VALUES ('20260819000033', '033_chat_enabled') ON CONFLICT DO NOTHING;
--
-- About 5 of the 63 accounts are senior sellers who have not worked a chat in
-- months. That is their permanent state, not a pause: management does not want
-- them receiving chats and they do not want to. There is no "partial" — either
-- you are in the shared system or you are not in it at all.
-- is_ready is the agent's own button and stays untouched. Presence itself comes
-- from the heartbeat pulse.
-- ============================================================
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS chat_enabled boolean NOT NULL DEFAULT true;
COMMENT ON COLUMN public.users.chat_enabled IS
  'Management-controlled: is this account part of the shared chat system at all? '
  'The agent cannot change it. Presence comes from the heartbeat pulse, not a button.';
CREATE INDEX IF NOT EXISTS idx_users_chat_enabled
  ON public.users (chat_enabled) WHERE chat_enabled = false;
