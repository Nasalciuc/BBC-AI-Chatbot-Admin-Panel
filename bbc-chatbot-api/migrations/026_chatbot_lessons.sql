-- 026_chatbot_lessons.sql — the daily learning loop's memory.
-- Execute in Supabase SQL Editor. Idempotent: safe to re-run.
-- NOT auto-applied by the app — the owner runs this manually (repo convention).
--
-- Two tables:
--   chatbot_lessons — what the loop learned, one row per pattern. Lessons land
--                     as 'proposed'; only 'approved' ones reach the live prompt.
--   learning_runs   — one row per run: what it looked at, what it cost, the tag
--                     distribution snapshot that makes the hill-climbing visible.

CREATE TABLE IF NOT EXISTS public.chatbot_lessons (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind                  text NOT NULL CHECK (kind IN ('killer_pattern','winning_pattern','few_shot')),
    title                 text NOT NULL,          -- short handle, e.g. "phone asked before value shown"
    content               text NOT NULL,          -- the lesson, actionable, NO PII
    evidence_count        int  NOT NULL DEFAULT 1,-- conversations supporting it
    denominator           int,                    -- size of the relevant tag population
    dominant_segment      text,                   -- e.g. "sales / kayak-cpc"
    contradicts_lesson_id uuid REFERENCES public.chatbot_lessons(id) ON DELETE SET NULL,
    sample_cards          jsonb,                  -- up to 3 masked cards for the approval screen
    status                text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','approved','retired')),
    first_seen_run        date NOT NULL,
    last_seen_run         date NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);

-- Columns added after the first draft — guarded so an early run can catch up.
ALTER TABLE public.chatbot_lessons ADD COLUMN IF NOT EXISTS denominator int;
ALTER TABLE public.chatbot_lessons ADD COLUMN IF NOT EXISTS dominant_segment text;
ALTER TABLE public.chatbot_lessons ADD COLUMN IF NOT EXISTS contradicts_lesson_id uuid;
ALTER TABLE public.chatbot_lessons ADD COLUMN IF NOT EXISTS sample_cards jsonb;

-- The prompt-injection query: approved, recently reinforced, best evidence first.
CREATE INDEX IF NOT EXISTS idx_chatbot_lessons_injection
    ON public.chatbot_lessons(status, last_seen_run DESC, evidence_count DESC);

-- The approval screen: proposed first, contradictions surfaced at the top.
CREATE INDEX IF NOT EXISTS idx_chatbot_lessons_status
    ON public.chatbot_lessons(status, created_at DESC);

CREATE TABLE IF NOT EXISTS public.learning_runs (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date                date NOT NULL,
    bootstrap               boolean NOT NULL DEFAULT false,
    conversations_analyzed  int NOT NULL DEFAULT 0,
    tag_distribution        jsonb,   -- {"completed":N,"abandoned":N,"no_engagement":N,...}
    new_lessons             int NOT NULL DEFAULT 0,
    reinforced_lessons      int NOT NULL DEFAULT 0,
    cost                    numeric(10,4) NOT NULL DEFAULT 0,
    status                  text NOT NULL DEFAULT 'ok',
    error                   text,
    created_at              timestamptz NOT NULL DEFAULT now()
);

-- One run per day: the idempotency guard reads this.
CREATE INDEX IF NOT EXISTS idx_learning_runs_date
    ON public.learning_runs(run_date DESC, status);

-- RLS — the API connects with the service_role key, which BYPASSES RLS.
-- Mirrors 021_teams.sql / 022_blocklist.sql: explicit policy so intent is stated.
ALTER TABLE public.chatbot_lessons ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chatbot_lessons_service_role_all ON public.chatbot_lessons;
CREATE POLICY chatbot_lessons_service_role_all
  ON public.chatbot_lessons
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);

ALTER TABLE public.learning_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS learning_runs_service_role_all ON public.learning_runs;
CREATE POLICY learning_runs_service_role_all
  ON public.learning_runs
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);
