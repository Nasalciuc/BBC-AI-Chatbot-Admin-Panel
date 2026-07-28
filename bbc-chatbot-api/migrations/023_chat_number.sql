-- Migration 023: human-readable chat number.
--
-- UUIDs are unusable out loud. Supervisors and operators need to say
-- "chat 1042" on a call, so every conversation gets a stable sequential
-- number. Backfilled oldest -> newest, so #1 really is the first
-- conversation the system ever had and existing chats keep an identity
-- consistent with their age.
--
-- Not the primary key, not a foreign key: it is a label. The UUID stays
-- the identifier everywhere in code.

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS chat_number bigint;

CREATE SEQUENCE IF NOT EXISTS conversations_chat_number_seq;

-- Backfill in creation order. Idempotent: only fills NULLs, so re-running
-- after new rows have been numbered by the default cannot renumber them.
WITH ordered AS (
    SELECT id,
           row_number() OVER (ORDER BY created_at ASC, id ASC) AS rn
    FROM conversations
    WHERE chat_number IS NULL
)
UPDATE conversations c
SET chat_number = o.rn
FROM ordered o
WHERE c.id = o.id;

-- Park the sequence past the highest number handed out, so the first new
-- conversation continues the series instead of colliding with a backfill.
SELECT setval(
    'conversations_chat_number_seq',
    COALESCE((SELECT MAX(chat_number) FROM conversations), 0) + 1,
    false
);

ALTER TABLE conversations
    ALTER COLUMN chat_number SET DEFAULT nextval('conversations_chat_number_seq');

ALTER SEQUENCE conversations_chat_number_seq OWNED BY conversations.chat_number;

-- Unique so a numbering bug surfaces here instead of in a support call
-- where two chats answer to the same number.
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_chat_number
    ON conversations (chat_number);
