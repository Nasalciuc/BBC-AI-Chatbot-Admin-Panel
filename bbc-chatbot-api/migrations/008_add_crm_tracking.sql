-- Migration: Add CRM tracking and contact country code fields
-- Created: April 2026
-- Purpose: Track CRM lead creation status and phone country codes

-- 1. Add created_in_crm flag to leads table
ALTER TABLE leads ADD COLUMN IF NOT EXISTS created_in_crm boolean NOT NULL DEFAULT false;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS created_in_crm_at timestamptz;

-- 2. Add country_code to conversations for phone number tracking
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS visitor_phone_country varchar(3);

-- 3. Create index for CRM lead lookups
CREATE INDEX IF NOT EXISTS idx_leads_created_in_crm ON leads(created_in_crm);

-- 4. Create index for searching by conversation phone + email
CREATE INDEX IF NOT EXISTS idx_conversations_phone_email 
  ON conversations(visitor_phone, visitor_email) 
  WHERE visitor_phone IS NOT NULL OR visitor_email IS NOT NULL;

-- 5. Create index for CRM conversion tracking
CREATE INDEX IF NOT EXISTS idx_leads_crm_converted 
  ON leads(created_in_crm, status, updated_at DESC) 
  WHERE created_in_crm = true;
