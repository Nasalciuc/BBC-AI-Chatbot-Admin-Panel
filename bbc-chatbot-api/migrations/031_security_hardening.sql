-- ============================================================
-- 031 — SECURITY HARDENING + PRESENCE PATCH RPC
-- Rulează DUPĂ 030. FILE ONLY — apply it yourself in the SQL editor.
--
-- Every claim below was checked against the LIVE database before this
-- file was written (pg_proc / pg_class), not assumed from the repo:
--   * all nine functions exist with exactly these identity signatures;
--   * blocklist ALREADY has RLS enabled (022 did it) — the statement
--     here is a harmless idempotent re-assert, NOT a fix;
--   * user_role() / user_tunnel_scope() carry `=X/postgres` in their
--     ACL, i.e. the implicit grant to PUBLIC. Revoking from `anon`
--     alone is a NO-OP: anon is a member of PUBLIC and keeps EXECUTE
--     through it. The revoke below therefore names PUBLIC.
--   * barem_recent_events(timestamptz, integer) exists, is SECURITY
--     DEFINER and is already search_path-pinned.
--
-- Pre-flight (run it, don't trust this comment):
--   SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid),
--          p.prosecdef, p.proconfig
--     FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
--    WHERE n.nspname = 'public'
--      AND p.proname IN ('user_role','user_tunnel_scope','log_message_delete',
--                        'log_conversation_delete','fn_update_timestamp',
--                        'fn_increment_message_count','fn_accumulate_ai_cost',
--                        'fn_sync_lead_derived','update_conv_presence');
-- ============================================================

-- 1. blocklist RLS — already on; re-asserted so a rebuilt environment
--    can never come up without it.
ALTER TABLE public.blocklist ENABLE ROW LEVEL SECURITY;

-- 2. The role helpers stop answering anonymous callers ---------
--    FROM PUBLIC is the part that actually does it. authenticated and
--    service_role hold their own ACL entries and survive the revoke;
--    they are re-granted explicitly so a fresh environment behaves the
--    same and RLS policy evaluation never loses EXECUTE.
REVOKE EXECUTE ON FUNCTION public.user_role()          FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.user_tunnel_scope()  FROM PUBLIC, anon;
GRANT  EXECUTE ON FUNCTION public.user_role()          TO authenticated, service_role;
GRANT  EXECUTE ON FUNCTION public.user_tunnel_scope()  TO authenticated, service_role;

-- 3. Pin search_path per function ------------------------------
-- NOTE on 'public' — NOT '': the trigger bodies reference unqualified
-- names (conversations, leads, messages); an empty search_path would
-- break them at runtime, a worse outage than the risk being closed.
-- NOTE on durability: `CREATE OR REPLACE FUNCTION` DROPS a function's
-- proconfig. Any future migration that replaces one of these MUST
-- re-apply its SET search_path (see update_conv_presence below, which
-- carries it inline for exactly that reason).
ALTER FUNCTION public.fn_update_timestamp()             SET search_path = 'public';
ALTER FUNCTION public.fn_increment_message_count()      SET search_path = 'public';
ALTER FUNCTION public.fn_accumulate_ai_cost()           SET search_path = 'public';
ALTER FUNCTION public.fn_sync_lead_derived()            SET search_path = 'public';
ALTER FUNCTION public.log_message_delete()              SET search_path = 'public';
ALTER FUNCTION public.log_conversation_delete()         SET search_path = 'public';
ALTER FUNCTION public.user_role()                       SET search_path = 'public';
ALTER FUNCTION public.user_tunnel_scope()               SET search_path = 'public';
ALTER FUNCTION public.update_conv_presence(uuid, jsonb) SET search_path = 'public';

-- 4. Presence PATCH — the heartbeat must not clobber other writers
--    The widget pings every 30s. Reading the whole metadata blob and
--    writing it back would silently revert any flag another request
--    claimed in between (closing_sent_at, super_notified_at,
--    confirmed_at, summary state…) — 120 chances per conversation per
--    hour to erase a claim. This merges ONLY the keys it is given,
--    inside the database, and keeps 029's no-touch discipline so a
--    heartbeat never resurrects a conversation up the panel's list.
CREATE OR REPLACE FUNCTION public.patch_conv_presence(
    p_conversation_id uuid,
    p_patch jsonb
)
RETURNS void
LANGUAGE plpgsql
SET search_path = 'public'   -- re-declared here: CREATE OR REPLACE drops proconfig
AS $$
BEGIN
    PERFORM set_config('app.skip_touch', '1', true);  -- true = SET LOCAL
    UPDATE conversations
       SET metadata = COALESCE(metadata, '{}'::jsonb) || p_patch
     WHERE id = p_conversation_id;
END;
$$;

-- barem_recent_events: QM-named stray in the BBC project — verify prosrc, likely DROP
--   SELECT prosrc FROM pg_proc WHERE proname = 'barem_recent_events';
-- (Confirmed live: SECURITY DEFINER, already search_path-pinned, and
-- referenced nowhere in this repository.)
