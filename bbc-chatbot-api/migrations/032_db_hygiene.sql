-- ============================================================
-- 032 — DB HYGIENE (19 Aug 2026 production audit)
-- FILE ONLY — apply manually in the SQL editor, then register:
--   INSERT INTO supabase_migrations.schema_migrations (version, name)
--   VALUES ('20260819000032', '032_db_hygiene') ON CONFLICT DO NOTHING;
--
-- Every claim below was checked against the LIVE database on 19 Aug
-- (information_schema / pg_class), not assumed from the repo.
-- ============================================================

-- 1. is_ready default drift — THE GHOST SEEDER.
--    Migration 016 set DEFAULT false. Production shows DEFAULT true: someone
--    reverted it. Every NEW account is therefore born "ready", which is exactly
--    how June's 46 phantom accounts appeared (is_ready=true, last_seen_at=NULL,
--    73% of auto-assigns ending with zero agent messages). is_ready is still a
--    gate in the legacy first-message routing (get_available_agents), so the
--    default must be false: readiness is claimed, never inherited.
ALTER TABLE public.users ALTER COLUMN is_ready SET DEFAULT false;

--    One-shot cleanup of ghosts seeded since the drift. Same rule as the June
--    cleanup: ready with no pulse ever = ghost. Idempotent. (Recounted live on
--    19 Aug: currently 0 rows match — the UPDATE is a no-op today and a net
--    for any account created between this audit and the apply.)
UPDATE public.users SET is_ready = false
WHERE is_ready = true AND last_seen_at IS NULL;

-- 2. audit_log — exists in production with NO repo migration. Formalised here
--    so repo == production and a rebuilt environment gets it. IF NOT EXISTS
--    makes this a no-op against the live database. Columns match production
--    exactly (verified via information_schema on 19 Aug).
CREATE TABLE IF NOT EXISTS public.audit_log (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid,
    user_email   text,
    action       text NOT NULL,
    target_table text NOT NULL,
    target_id    uuid,
    details      jsonb DEFAULT '{}'::jsonb,
    ip_address   text,
    created_at   timestamptz DEFAULT now()
);
ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;  -- already on live; re-assert

-- 3. QM strays inside the BBC project. Verified referenced NOWHERE in this
--    repository (grep), and named by 031's own comment as "likely DROP".
--    barem_events holds 263 rows of QM telemetry (recounted live, 19 Aug) —
--    export first if anyone wants them; nobody has asked in five weeks.
DROP FUNCTION IF EXISTS public.barem_recent_events(timestamptz, integer);
DROP TABLE IF EXISTS public.barem_events;

-- NOT touched here, recorded so the next reader does not "fix" them:
--   * app_settings / schema_version — defined in 001, unused by the code.
--     Dropping them is cosmetic and waits for its own ticket.
--   * blocklist has RLS ON with ZERO policies. That is DELIBERATE: with no
--     policy, RLS denies everything for anon/authenticated, while the backend
--     passes because it uses the service_role key. The blocklist is
--     backend-only by design. Reading it from the panel through the Supabase
--     client returns zero rows — correct behaviour, not an empty table.
