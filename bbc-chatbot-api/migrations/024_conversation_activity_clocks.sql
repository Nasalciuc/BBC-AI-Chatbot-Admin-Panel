-- Migration 024: who spoke last, and when.
--
-- The auto-derived tags (Fresh / Main Queue / Active / Inactive) need to
-- know whether the customer or our side spoke last, and how long ago.
-- That is answerable from `messages`, but only per conversation - which on
-- a list poll every 5 seconds is an N+1 over the whole page. These three
-- columns are a denormalised cache of that answer, maintained on insert in
-- db.add_message.
--
-- Why three and not one:
--   last_user_message_at  - the customer's silence is measured from here
--   last_agent_message_at - a HUMAN operator spoke (AI does not count)
--   last_reply_at         - anything outbound (ai or agent)
--
-- The distinction between the last two is what keeps "the customer went
-- quiet" separate from "we never answered". The AI answers instantly, so
-- last_reply_at is almost always fresh; only last_reply_at >= 
-- last_user_message_at proves the customer is the one who stopped.

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS last_user_message_at  timestamptz,
    ADD COLUMN IF NOT EXISTS last_agent_message_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_reply_at         timestamptz;

-- Backfill from history so tags are correct for conversations that existed
-- before this feature. One pass over messages, grouped.
WITH activity AS (
    SELECT conversation_id,
           MAX(created_at) FILTER (WHERE role = 'user')             AS last_user,
           MAX(created_at) FILTER (WHERE role = 'agent')            AS last_agent,
           MAX(created_at) FILTER (WHERE role IN ('ai', 'agent'))   AS last_reply
    FROM messages
    GROUP BY conversation_id
)
UPDATE conversations c
SET last_user_message_at  = activity.last_user,
    last_agent_message_at = activity.last_agent,
    last_reply_at         = activity.last_reply
FROM activity
WHERE c.id = activity.conversation_id;

-- The Inactive section asks exactly one question: which customers have been
-- silent since before <cutoff>. Oldest silence first.
CREATE INDEX IF NOT EXISTS idx_conversations_last_user_message_at
    ON conversations (last_user_message_at);
