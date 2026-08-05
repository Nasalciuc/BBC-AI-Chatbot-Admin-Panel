-- 028_learning_hardening.sql — guards that must land before the bootstrap.
-- Execute in Supabase SQL Editor together with 026 (+ 027 if not yet applied).
-- Idempotent: safe to re-run.
--
-- needs_scrutiny  — sanitizer flagged the lesson content (imperative overrides,
--                   URLs, second-person model instructions, residual PII).
--                   Still inserts as proposed; approving requires an explicit
--                   acknowledge_scrutiny flag on the PATCH.
-- active_lesson_ids — sorted list of approved lesson ids at the start of the
--                   run. Day-granularity provenance so hill climbing can see
--                   which lessons were live on a given day.

ALTER TABLE public.chatbot_lessons
  ADD COLUMN IF NOT EXISTS needs_scrutiny boolean NOT NULL DEFAULT false;

ALTER TABLE public.learning_runs
  ADD COLUMN IF NOT EXISTS active_lesson_ids jsonb NOT NULL DEFAULT '[]'::jsonb;
