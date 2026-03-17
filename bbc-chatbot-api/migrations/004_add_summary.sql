ALTER TABLE conversations ADD COLUMN IF NOT EXISTS summary text;
COMMENT ON COLUMN conversations.summary IS 'Auto-generated 2-sentence summary via Haiku';
