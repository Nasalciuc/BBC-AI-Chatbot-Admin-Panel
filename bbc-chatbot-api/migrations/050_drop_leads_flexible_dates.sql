-- 050 (PR-14): leads.flexible_dates goes. PR-7 replaced it with
-- dates_flexibility (040); since this PR no code reads or writes it.
--
-- ORDER: deploy the code of this PR FIRST, then run this file. An older
-- build still selects the column and would fail on every lead read.
--
-- APPLIED: nu.

-- Review fix: carry the one value that says something across first. The
-- column was NOT NULL DEFAULT true (001), so `true` is also every lead whose
-- visitor was never asked — it cannot be told apart from "flexible" and maps
-- to nothing (dates_flexibility stays NULL, read as unknown). `false` was
-- only ever set on purpose: it becomes 'exact'. A value 040's code already
-- wrote is kept. Guarded, so a re-run after the drop is a no-op.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'leads'
                  AND column_name = 'flexible_dates') THEN
        UPDATE public.leads
           SET dates_flexibility = 'exact'
         WHERE dates_flexibility IS NULL
           AND flexible_dates = false;
    END IF;
END $$;

-- destructive: leads.flexible_dates is deprecated since PR-7 (dates_flexibility replaces it) and no code path reads it after PR-14
ALTER TABLE public.leads DROP COLUMN IF EXISTS flexible_dates;
