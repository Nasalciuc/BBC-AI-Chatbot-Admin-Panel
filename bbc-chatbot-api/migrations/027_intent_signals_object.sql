-- 027_intent_signals_object.sql — normalize leads.intent_signals to an OBJECT.
-- Execute in Supabase SQL Editor. Idempotent: safe to re-run.
-- NOT auto-applied by the app — the owner runs this manually (repo convention).
--
-- History: the column was born as a jsonb ARRAY default ('[]'). It has only
-- ever held either that empty default or (post-#161 Mason DNA) an OBJECT of
-- methodology signals ({"occasion":…,"persona":…}). The Pydantic model now
-- accepts both shapes so GET /leads/{id} stops 500-ing; this migration makes
-- the column one thing forever.
--
-- Trigger discipline: leads has trg_leads_updated_at (migration 002). Without
-- disabling it, every touched row would get updated_at = now() — the same
-- "everything says 6m ago" incident we hit on conversations after 023/024.

BEGIN;
ALTER TABLE leads DISABLE TRIGGER trg_leads_updated_at;

UPDATE leads SET intent_signals = '{}'::jsonb
WHERE jsonb_typeof(intent_signals) = 'array';

ALTER TABLE leads ALTER COLUMN intent_signals SET DEFAULT '{}'::jsonb;

ALTER TABLE leads ENABLE TRIGGER trg_leads_updated_at;
COMMIT;
