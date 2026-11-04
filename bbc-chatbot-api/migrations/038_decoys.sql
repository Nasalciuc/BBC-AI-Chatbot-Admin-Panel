-- 038: decoy flags. A decoy account/lead/conversation looks real in the
-- database but is filtered out of every list, queue, cron, CRM push and
-- learning card ON THE SERVER, so no operator or job ever touches it. The only
-- thing that touches it is an intruder working from a stolen dump — which is
-- exactly the signal we want.
--
-- APPLIED: nu. The code treats a missing column as "no decoys": the filter
-- `.eq("is_decoy", False)` is only added when the column exists (feature flag
-- DECOYS_ENABLED gates the behaviour, and reads tolerate the column's absence).

ALTER TABLE users         ADD COLUMN IF NOT EXISTS is_decoy boolean NOT NULL DEFAULT false;
ALTER TABLE leads         ADD COLUMN IF NOT EXISTS is_decoy boolean NOT NULL DEFAULT false;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS is_decoy boolean NOT NULL DEFAULT false;
