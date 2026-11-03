-- 050 (PR-14): leads.flexible_dates goes. PR-7 replaced it with
-- dates_flexibility (040); since this PR no code reads or writes it.
--
-- ORDER: deploy the code of this PR FIRST, then run this file. An older
-- build still selects the column and would fail on every lead read.
--
-- APPLIED: nu.

-- destructive: leads.flexible_dates is deprecated since PR-7 (dates_flexibility replaces it) and no code path reads it after PR-14
ALTER TABLE public.leads DROP COLUMN IF EXISTS flexible_dates;
