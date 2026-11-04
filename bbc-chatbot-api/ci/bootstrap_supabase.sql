-- ci/bootstrap_supabase.sql (PR-14)
--
-- An empty postgres:16 has none of what a Supabase project starts with, and
-- the migrations touch it (RLS policies TO service_role, GRANT/REVOKE to
-- anon/authenticated, auth.uid() in policies). This creates the minimum so
-- the migrations replay on a clean database in CI. It is NOT applied to the
-- real project — Supabase already has all of it.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
    END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS storage;
CREATE SCHEMA IF NOT EXISTS extensions;

-- The two auth helpers policies call. Supabase's read the request's JWT;
-- here they answer NULL, which is all a schema replay needs.
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT NULL::uuid $$;
CREATE OR REPLACE FUNCTION auth.role() RETURNS text LANGUAGE sql STABLE AS $$ SELECT NULL::text $$;
CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS $$ SELECT '{}'::jsonb $$;

CREATE TABLE IF NOT EXISTS storage.buckets (
    id text PRIMARY KEY, name text, public boolean DEFAULT false, created_at timestamptz DEFAULT now()
);

GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
