-- Migration 010: user access audit trail

CREATE TABLE IF NOT EXISTS user_access_audit (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    target_user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    changed_by_user_id  uuid REFERENCES users(id) ON DELETE SET NULL,
    action              varchar(30) NOT NULL DEFAULT 'access_update',
    old_role            varchar(20),
    new_role            varchar(20),
    old_tunnel_scope    varchar(20),
    new_tunnel_scope    varchar(20),
    old_is_active       boolean,
    new_is_active       boolean,
    changed_fields      jsonb NOT NULL DEFAULT '[]',
    note                text,
    created_at          timestamptz NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_access_audit_target
    ON user_access_audit(target_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_access_audit_actor
    ON user_access_audit(changed_by_user_id, created_at DESC);
