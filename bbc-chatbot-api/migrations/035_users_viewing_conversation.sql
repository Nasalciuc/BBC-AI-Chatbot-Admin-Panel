-- 035: what the operator is looking at, written by the heartbeat, read by the
-- scheduler sweep. The "operator is viewing this conversation → extend the
-- first-response deadline to 120s" rule used to live inside the heartbeat
-- because only the heartbeat knew the answer. Moving the sweep out of the
-- heartbeat (it ran 7×/s across 35 agents) means the answer has to be
-- persisted. Nullable: NULL = viewing nothing. Stale values are harmless —
-- the sweep only honours it when last_seen_at is fresh (<30s).
ALTER TABLE users ADD COLUMN IF NOT EXISTS viewing_conversation_id uuid NULL;
