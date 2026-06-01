-- Idempotent: ensure children_count/infant_count default to 0 (014 may already apply)
ALTER TABLE leads ALTER COLUMN children_count SET DEFAULT 0;
ALTER TABLE leads ALTER COLUMN infant_count SET DEFAULT 0;
