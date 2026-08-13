-- ============================================================
-- 030 — CRM PUSH TRUTH
-- Rulează DUPĂ 029.
--
-- crm_lead_id: the PROOF. created_in_crm may only be true when the CRM
-- answered 2xx AND returned an id — that id lives here. A flag without
-- an id is a claim; a flag with an id is a receipt (#1259 pessaint: the
-- flag went true on a validation rejection).
--
-- crm_push_attempts: the orphan backstop's counter — gold leads whose
-- push was halted (human takeover, transient CRM failure) get retried
-- every 30 min, max 3 attempts, then surface in the panel's
-- "CRM pending" work-list instead of rotting silently (#1364 Paulette,
-- 24 of 617 gold leads orphaned).
-- ============================================================

ALTER TABLE leads ADD COLUMN IF NOT EXISTS crm_lead_id text;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS crm_push_attempts integer NOT NULL DEFAULT 0;

-- Refusals are never silent: a lead the quality gate refuses (test@,
-- malformed email, score<40, origin==destination) carries its reason
-- here and shows up in the same "CRM pending" work-list — visible
-- state, human override possible. Cleared on a successful push.
ALTER TABLE leads ADD COLUMN IF NOT EXISTS crm_push_gate_reason text;
