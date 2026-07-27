-- 022_blocklist.sql — abuse blocklist (IP + phone + email)
-- Execute in Supabase SQL Editor. Idempotent: safe to re-run.
-- NOT auto-applied by the app — the owner runs this manually (repo convention).
--
-- Matching policy (enforced in app/services/blocklist.py, NOT in SQL):
--   phone match  -> blocked
--   email match  -> blocked
--   ip match     -> NEVER blocks on its own. IPs are shared (offices, hotels,
--                   mobile carriers), so blocking on IP alone would refuse
--                   innocent visitors. IP rows are stored for audit/visibility
--                   and expire by default (settings.blocklist_ip_ttl_days).

CREATE TABLE IF NOT EXISTS public.blocklist (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            varchar(10) NOT NULL CHECK (kind IN ('ip', 'phone', 'email')),
    value           text NOT NULL,        -- normalized: phone=digits only, email=lowercased, ip=trimmed
    reason          text,
    blocked_by      uuid REFERENCES public.users(id) ON DELETE SET NULL,
    conversation_id uuid,                 -- where the block originated (context, not a FK constraint)
    expires_at      timestamptz,          -- NULL = permanent (phone/email default)
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- One row per (kind, value): re-blocking updates the existing entry (upsert).
CREATE UNIQUE INDEX IF NOT EXISTS idx_blocklist_kind_value
    ON public.blocklist(kind, value);

-- Lookup index for the hot path (is this identifier blocked right now?).
CREATE INDEX IF NOT EXISTS idx_blocklist_active
    ON public.blocklist(kind, value)
    WHERE expires_at IS NULL OR expires_at > now();

CREATE INDEX IF NOT EXISTS idx_blocklist_conversation
    ON public.blocklist(conversation_id);

-- RLS — the API connects with the service_role key, which BYPASSES RLS.
-- Mirrors 021_teams.sql: explicit service_role policy so intent is stated.
ALTER TABLE public.blocklist ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS blocklist_service_role_all ON public.blocklist;
CREATE POLICY blocklist_service_role_all
  ON public.blocklist
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);
