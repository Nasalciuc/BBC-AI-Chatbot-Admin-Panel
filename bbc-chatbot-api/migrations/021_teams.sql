-- 021_teams.sql — Teams (group + shift + one supervisor) and the project_manager role
-- Execute in Supabase SQL Editor. Idempotent: safe to re-run.
-- NOT auto-applied by the app — the owner runs this manually (repo convention).

-- 1) Extend users.role CHECK with 'project_manager'.
--    Drop the known-named constraint first (Postgres normalizes `role IN (...)`
--    to `role = ANY (ARRAY...)`, so the old 011/013 `ILIKE '%role IN%'` discovery
--    no longer matches on this DB). A corrected discovery fallback covers any
--    differently-named role check. Final allowed set:
--    ('owner','admin','dev','sales','support','supervisor','qa','project_manager')
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE t.relname = 'users'
      AND n.nspname = current_schema()
      AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) ILIKE '%role%'
      AND pg_get_constraintdef(c.oid) NOT ILIKE '%tunnel%';
    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE users DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('owner', 'admin', 'dev', 'sales', 'support', 'supervisor', 'qa', 'project_manager'));

-- 2) teams — a group of operators + its own working shift + exactly ONE supervisor
CREATE TABLE IF NOT EXISTS public.teams (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  shift_name    text,                    -- optional label, e.g. 'Morning'
  shift_start   time,                    -- e.g. 08:00 (informational in Phase 1)
  shift_end     time,                    -- e.g. 16:00; night shift may have end < start (wrap)
  supervisor_id uuid REFERENCES public.users(id) ON DELETE SET NULL,   -- exactly ONE per team
  pm_id         uuid REFERENCES public.users(id) ON DELETE SET NULL,
  is_active     boolean NOT NULL DEFAULT true,
  created_by    uuid REFERENCES public.users(id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- 3) membership + monitoring stamps (nullable → inert on existing data)
ALTER TABLE public.users         ADD COLUMN IF NOT EXISTS team_id uuid REFERENCES public.teams(id) ON DELETE SET NULL;
ALTER TABLE public.conversations ADD COLUMN IF NOT EXISTS team_id uuid REFERENCES public.teams(id) ON DELETE SET NULL;
ALTER TABLE public.leads         ADD COLUMN IF NOT EXISTS team_id uuid REFERENCES public.teams(id) ON DELETE SET NULL;

-- 4) indexes
CREATE INDEX IF NOT EXISTS idx_teams_supervisor   ON public.teams(supervisor_id);
CREATE INDEX IF NOT EXISTS idx_teams_pm           ON public.teams(pm_id);
CREATE INDEX IF NOT EXISTS idx_users_team         ON public.users(team_id);
CREATE INDEX IF NOT EXISTS idx_conversations_team ON public.conversations(team_id);
CREATE INDEX IF NOT EXISTS idx_leads_team         ON public.leads(team_id);

-- 5) RLS — the API connects with the service_role key, which BYPASSES RLS.
--    No other table in this repo declares RLS/policies (they all rely on the
--    service_role bypass). We still enable RLS on teams and add an explicit
--    service_role FOR ALL policy so behavior is identical to the rest of the
--    schema while being explicit about intent. Idempotent via DROP IF EXISTS.
ALTER TABLE public.teams ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS teams_service_role_all ON public.teams;
CREATE POLICY teams_service_role_all
  ON public.teams
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);
