-- 045 (PR-14): the panel's list — status filter, newest first.
--
-- CREATE INDEX CONCURRENTLY cannot run inside a transaction: this file has
-- no BEGIN/COMMIT on purpose. Run it alone in the SQL editor (the editor
-- would otherwise wrap it). CI replays it with psql, without
-- --single-transaction. It does not lock writes while it builds.
--
-- APPLIED: nu.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversations_status_updated
    ON public.conversations (status, updated_at DESC);
