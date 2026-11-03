-- 039: token_version bumps invalidate a user's live sessions at once.
--
-- A role change or a deactivation used to take effect only when the token
-- expired (up to 24h). The version is put in the JWT and checked on every
-- request against the users row (cached 30-60s); a mismatch is a 401. Raising
-- the version (on role/scope change or deactivation) logs everyone out now.
--
-- APPLIED: nu. A missing column reads as version 0 everywhere (tokens with no
-- iat/version still validate), so the check is inert until applied.
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version int NOT NULL DEFAULT 0;
