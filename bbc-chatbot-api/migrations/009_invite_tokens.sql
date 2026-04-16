-- Migration 009: Invite magic link tokens (one-time, 30m expiry)

CREATE TABLE IF NOT EXISTS invite_tokens (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token           text NOT NULL UNIQUE,
    purpose         varchar(30) NOT NULL DEFAULT 'set_password'
                    CHECK (purpose IN ('set_password')),
    expires_at      timestamptz NOT NULL,
    used_at         timestamptz,
    created_by      uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invite_tokens_user_id ON invite_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_invite_tokens_expires_at ON invite_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_invite_tokens_active
    ON invite_tokens(user_id, purpose, expires_at DESC)
    WHERE used_at IS NULL;
