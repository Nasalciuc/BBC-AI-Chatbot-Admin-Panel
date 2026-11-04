-- 048 (PR-14): indexes another index already covers. Each one costs a write
-- on every insert/update and buys no read:
--   idx_msg_conv_id   (messages.conversation_id)   ⊂ idx_msg_conv_created (conversation_id, created_at)
--   idx_leads_conv_id (leads.conversation_id)      ⊂ leads_conversation_id_key (UNIQUE)
--   idx_conv_tunnel   (conversations.tunnel)       ⊂ idx_conv_tunnel_status (tunnel, status)
--   idx_leads_status  (leads.status)               ⊂ idx_leads_status_tier (status, tier)
-- Checked on a schema replayed from migrations; check the live definitions
-- the same way before running (\d+ or pg_indexes). No data is removed.
--
-- DROP INDEX CONCURRENTLY — no BEGIN/COMMIT on purpose; run each alone.
--
-- APPLIED: nu.
DROP INDEX CONCURRENTLY IF EXISTS public.idx_msg_conv_id;
DROP INDEX CONCURRENTLY IF EXISTS public.idx_leads_conv_id;
DROP INDEX CONCURRENTLY IF EXISTS public.idx_conv_tunnel;
DROP INDEX CONCURRENTLY IF EXISTS public.idx_leads_status;
