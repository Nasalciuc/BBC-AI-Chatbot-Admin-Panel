-- 040: richer trip detail on the lead (PR-7).
--
-- trip_notes holds the soft facts the consultant needs but the CRM has no
-- column for (date windows, alternate airports, cabin request, children ages,
-- mobility, pets, contact preference, undecided) AND trip_type_source, which
-- is how the slot policy tells a client-stated trip type from the NOT NULL
-- 'round_trip' default. adults is the adult count; dates_flexibility is the
-- window kind. flexible_dates is left untouched (deprecated; dropped in PR-14).
--
-- APPLIED: nu. Missing columns read as absent and the slot policy falls back
-- to today's behaviour.
ALTER TABLE leads ADD COLUMN IF NOT EXISTS trip_notes jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS adults int;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS dates_flexibility text
    CHECK (dates_flexibility IN ('exact','flexible','window','unknown'));
