-- QA review tracking for leads
-- Allows QA auditors to mark which leads/conversations they have reviewed

ALTER TABLE leads ADD COLUMN IF NOT EXISTS reviewed_by_qa boolean NOT NULL DEFAULT false;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS reviewed_by uuid REFERENCES users(id);
ALTER TABLE leads ADD COLUMN IF NOT EXISTS qa_notes text;

-- Partial index for fast "unreviewed" filtering (audit queue)
CREATE INDEX IF NOT EXISTS idx_leads_reviewed_by_qa
  ON leads (reviewed_by_qa, created_at)
  WHERE reviewed_by_qa = false;
