-- 041: the self-learning loop v2 (PR-8).
--
-- question_variants — competing phrasings of each slot's question; the bandit
-- (app/learning/bandit.py) picks among status='active' ones. Seeded with the
-- measured defaults (origin='seed').
-- question_outcomes — one row per AI message that asked exactly one slot:
-- answered=NULL at ask time, set true/false after the next client message's
-- extraction. This is the reward signal.
-- learning_runs gains batch_id/phase/submitted_at for the Batch API lifecycle.
--
-- APPLIED: nu. Missing tables read as "no variants" (the slot uses its default
-- question) and "no outcomes" (the bandit has no data) — inert until applied.

CREATE TABLE IF NOT EXISTS public.question_variants (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slot        text NOT NULL,
    text        text NOT NULL,
    status      text NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed','active','retired')),
    origin      text NOT NULL DEFAULT 'learned'
                CHECK (origin IN ('seed','learned','human')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    retired_at  timestamptz
);
CREATE INDEX IF NOT EXISTS idx_question_variants_slot_status
    ON public.question_variants(slot, status);

CREATE TABLE IF NOT EXISTS public.question_outcomes (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    conversation_id uuid,
    message_id      uuid,
    slot            text NOT NULL,
    variant_id      uuid,
    segment         text,
    control         boolean NOT NULL DEFAULT false,
    answered        boolean           -- NULL until the next client message resolves it
);
CREATE INDEX IF NOT EXISTS idx_question_outcomes_slot
    ON public.question_outcomes(slot, variant_id);
CREATE INDEX IF NOT EXISTS idx_question_outcomes_open
    ON public.question_outcomes(conversation_id) WHERE answered IS NULL;

ALTER TABLE public.learning_runs ADD COLUMN IF NOT EXISTS batch_id text;
ALTER TABLE public.learning_runs ADD COLUMN IF NOT EXISTS phase text;
ALTER TABLE public.learning_runs ADD COLUMN IF NOT EXISTS submitted_at timestamptz;

-- RLS — service_role only, like the other learning tables (026).
ALTER TABLE public.question_variants ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS question_variants_service_role_all ON public.question_variants;
CREATE POLICY question_variants_service_role_all ON public.question_variants
  FOR ALL TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.question_outcomes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS question_outcomes_service_role_all ON public.question_outcomes;
CREATE POLICY question_outcomes_service_role_all ON public.question_outcomes
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- One row per (slot, text): the seed below can then be re-run safely, and a
-- duplicate phrasing can never compete with itself in the bandit.
CREATE UNIQUE INDEX IF NOT EXISTS question_variants_slot_text_uniq
  ON public.question_variants (slot, text);

-- Seed the measured default questions as active variants.
INSERT INTO public.question_variants (slot, text, status, origin) VALUES
  ('route',          'Where are you flying from and to?',       'active', 'seed'),
  ('departure_date', 'What date would you like to depart?',     'active', 'seed'),
  ('trip_type',      'Is this a round trip, or one way?',        'active', 'seed'),
  ('return_date',    'When would you like to fly back?',         'active', 'seed'),
  ('passengers',     'How many travelers will be on this trip?', 'active', 'seed')
ON CONFLICT (slot, text) DO NOTHING;
