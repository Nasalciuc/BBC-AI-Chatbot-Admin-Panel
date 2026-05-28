-- Add children/infant breakdown to leads (CRM payload split)
ALTER TABLE leads ADD COLUMN IF NOT EXISTS children_count int DEFAULT 0;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS infant_count int DEFAULT 0;
