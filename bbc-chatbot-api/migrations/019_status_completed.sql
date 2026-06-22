-- Add 'completed' status for post-CRM conversations that should not reopen.
-- 'closed' = abandoned/manual close (can reopen).
-- 'completed' = CRM submitted + closing sent (cannot reopen).

ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_status_check;
ALTER TABLE conversations ADD CONSTRAINT conversations_status_check
  CHECK (status IN ('active', 'pending', 'closed', 'completed'));

-- Backfill: existing closed conversations with closing_sent_at → completed
UPDATE conversations
SET status = 'completed'
WHERE status = 'closed'
  AND metadata->>'closing_sent_at' IS NOT NULL;
