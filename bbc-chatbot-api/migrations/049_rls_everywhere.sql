-- 049 (PR-14): row-level security on every public table, and no function
-- callable by PUBLIC.
--
-- The API connects as service_role, which BYPASSES RLS, so nothing the
-- chatbot or the panel does changes. What changes: the anon and
-- authenticated keys (the anon key is public by design) can no longer read
-- or write a table directly through PostgREST. Today 16 tables have RLS
-- off (user_access_audit, users, conversations, messages, leads, …).
--
-- RLS on with no policy for anon/authenticated = deny. service_role keeps
-- the explicit policy the newer tables already use.
--
-- APPLIED: nu.
DO $$
DECLARE
    t record;
BEGIN
    FOR t IN
        SELECT c.relname
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.relname);
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
             WHERE schemaname = 'public' AND tablename = t.relname
               AND 'service_role' = ANY (roles)
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I FOR ALL TO service_role USING (true) WITH CHECK (true)',
                t.relname || '_service_role_all', t.relname
            );
        END IF;
    END LOOP;
END $$;

-- Functions: nobody but the roles that need them. patch_conv_presence (031)
-- and every other public function lose the implicit EXECUTE for PUBLIC;
-- service_role keeps it explicitly. user_role()/user_tunnel_scope() keep
-- authenticated too (RLS policy evaluation), as 031 set.
DO $$
DECLARE
    f record;
BEGIN
    FOR f IN
        SELECT p.oid::regprocedure AS sig, p.proname
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.prokind = 'f'
    LOOP
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC, anon', f.sig);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO service_role', f.sig);
        IF f.proname NOT IN ('user_role', 'user_tunnel_scope') THEN
            EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM authenticated', f.sig);
        END IF;
    END LOOP;
END $$;

-- New functions created later stay closed unless a migration opens them.
-- Review fix: per-schema default privileges only ADD to the global ones, so
-- a per-schema REVOKE cannot take away the built-in EXECUTE for PUBLIC — the
-- global form does. anon and authenticated get their EXECUTE from Supabase's
-- own per-schema defaults, so those are revoked in the schema form. Both
-- apply to functions created by the role running this file (the owner's
-- `postgres` in the SQL editor, the role every later migration runs as).
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO service_role;
