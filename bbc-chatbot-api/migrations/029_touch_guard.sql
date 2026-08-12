-- ============================================================
-- 029 — TOUCH GUARD + PRESENCE RPC + ONE-ACTIVE-PER-VISITOR
-- Rulează DUPĂ 028. Trei probleme, o singură migrare:
--
--   F2  Presence writes (/open, /close, message⇒online) go through the
--       same UPDATE as real activity, so trg_conversations_updated_at
--       bumps updated_at and ghost conversations resurface at the top
--       of updated_at-sorted lists. Fix: transaction-local GUC
--       app.skip_touch — set ONLY inside update_conv_presence(), so
--       every other UPDATE on every other table keeps touching.
--
--   F3  Two /chat/start requests ~1s apart both miss Path B (neither
--       row committed yet) and both insert → twin active conversations
--       for one visitor. Path B already assumes one-active-per-visitor
--       (newest-active limit 1); enforce it with a partial unique
--       index. The API catches the unique violation and re-selects
--       the winner.
-- ============================================================

-- 1. Guard the shared timestamp trigger. current_setting(..., true)
--    returns NULL when the GUC was never set → guard is a no-op for
--    all existing traffic on users/conversations/kb/leads/app_settings.
CREATE OR REPLACE FUNCTION fn_update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('app.skip_touch', true) = '1' THEN
        RETURN NEW;
    END IF;
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 2. Presence-only write path. SET LOCAL scopes the skip to THIS
--    function's transaction — the trigger skips the touch, then the
--    GUC dies with the transaction. Called from the API via .rpc()
--    (a PostgREST UPDATE cannot carry a SET LOCAL, so rpc is the only
--    way to keep trigger + skip atomic).
CREATE OR REPLACE FUNCTION update_conv_presence(
    p_conversation_id uuid,
    p_metadata jsonb
)
RETURNS void AS $$
BEGIN
    PERFORM set_config('app.skip_touch', '1', true);  -- true = SET LOCAL
    UPDATE conversations
       SET metadata = p_metadata
     WHERE id = p_conversation_id;
END;
$$ LANGUAGE plpgsql;

-- 3. One active conversation per visitor.
--    First close existing twins (keep the newest — the row Path B
--    would pick), else the unique index cannot be created.
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY visitor_id ORDER BY created_at DESC
           ) AS rn
      FROM conversations
     WHERE status = 'active' AND visitor_id IS NOT NULL
)
UPDATE conversations c
   SET status = 'closed',
       metadata = COALESCE(c.metadata, '{}'::jsonb)
                  || '{"closed_reason": "twin_dedup_029"}'::jsonb
  FROM ranked r
 WHERE c.id = r.id AND r.rn > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_conv_active_visitor
    ON conversations (visitor_id)
 WHERE status = 'active' AND visitor_id IS NOT NULL;
