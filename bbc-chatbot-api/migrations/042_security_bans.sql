-- 042: a durable record of bans for the panel's Security → Bans page (PR-11).
--
-- The live ban state is in Redis (fast, TTL'd); this table is the audit trail
-- an operator reviews and can Unban from. Written alongside each ban, updated
-- on unban. RLS service_role only.
--
-- APPLIED: nu. Missing table → the guard still bans via Redis; only the panel
-- history is unavailable.

CREATE TABLE IF NOT EXISTS public.security_bans (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    identity        text NOT NULL,              -- ip, or key:<hash>
    jail            text NOT NULL,
    reason          text,
    last_paths      jsonb NOT NULL DEFAULT '[]'::jsonb,  -- last 10 paths seen
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz,
    ban_count       int NOT NULL DEFAULT 1,
    unbanned_by     text,
    unbanned_at     timestamptz
);
CREATE INDEX IF NOT EXISTS idx_security_bans_identity ON public.security_bans(identity);
CREATE INDEX IF NOT EXISTS idx_security_bans_active
    ON public.security_bans(expires_at) WHERE unbanned_at IS NULL;

ALTER TABLE public.security_bans ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS security_bans_service_role_all ON public.security_bans;
CREATE POLICY security_bans_service_role_all ON public.security_bans
  FOR ALL TO service_role USING (true) WITH CHECK (true);
