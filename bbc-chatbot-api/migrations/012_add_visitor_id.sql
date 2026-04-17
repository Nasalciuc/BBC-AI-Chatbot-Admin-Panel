-- 012_add_visitor_id.sql
-- Adds a stable visitor identity column for cross-session conversation matching.
-- Run in Supabase SQL Editor BEFORE deploying C2/C3 code changes.

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS visitor_id TEXT;

-- Index for the new visitor_id lookup path
CREATE INDEX IF NOT EXISTS idx_conversations_visitor_id
  ON conversations (visitor_id)
  WHERE visitor_id IS NOT NULL;
