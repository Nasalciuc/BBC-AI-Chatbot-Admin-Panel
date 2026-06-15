-- Agent presence + activity tracking (PR-PRESENCE)
-- Applied manually in Supabase before deploy.

CREATE TABLE IF NOT EXISTS agent_presence_daily (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day date NOT NULL,
  first_seen_at timestamptz NOT NULL,
  last_seen_at timestamptz NOT NULL,
  minutes_online int NOT NULL DEFAULT 0,
  heartbeat_ticks int NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day)
);
CREATE INDEX IF NOT EXISTS idx_presence_daily_day ON agent_presence_daily(day DESC);

CREATE TABLE IF NOT EXISTS agent_ready_log (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  is_ready boolean NOT NULL,
  changed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ready_log_user_time ON agent_ready_log(user_id, changed_at DESC);

CREATE TABLE IF NOT EXISTS agent_activity_log (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  conversation_id uuid REFERENCES conversations(id) ON DELETE SET NULL,
  action text NOT NULL,
  handoff_reason text,
  happened_at timestamptz NOT NULL DEFAULT now(),
  response_seconds int
);
CREATE INDEX IF NOT EXISTS idx_activity_user_day ON agent_activity_log(user_id, happened_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_conv ON agent_activity_log(conversation_id, happened_at);
