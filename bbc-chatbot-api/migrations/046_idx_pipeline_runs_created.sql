-- 046 (PR-14): the dashboard and retention read pipeline_runs by time.
--
-- CONCURRENTLY — no BEGIN/COMMIT on purpose; run it alone in the SQL editor.
--
-- APPLIED: nu.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pipeline_runs_created
    ON public.pipeline_runs (created_at DESC);
