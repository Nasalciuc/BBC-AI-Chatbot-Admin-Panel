-- 037: a row per Anthropic call, so the ledger can be reconciled against the
-- invoice. The bill is much larger than anything the app records, because the
-- cache tokens were never costed and several calls were never recorded at all.
--
-- ai_cost_daily holds the daily reconciliation: the usage API's figure per key
-- and model next to the sum of ai_calls for the same day. Other keys' rows are
-- kept — they show how much of the bill is NOT the chatbot.
--
-- APPLIED: nu. The code treats a missing table as "ledger disabled": one
-- warning, the reply still goes out.

CREATE TABLE IF NOT EXISTS ai_calls (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at              timestamptz NOT NULL DEFAULT now(),
    task                    text        NOT NULL,          -- classify, extract, sales_reply, summary, iata, closing, learn_map, …
    model                   text        NOT NULL,
    input_tokens            int         NOT NULL DEFAULT 0,
    output_tokens           int         NOT NULL DEFAULT 0,
    cache_write_tokens      int         NOT NULL DEFAULT 0,
    cache_read_tokens       int         NOT NULL DEFAULT 0,
    cost_usd                numeric(12,6) NOT NULL DEFAULT 0,
    latency_ms              int,
    status                  text        NOT NULL DEFAULT 'ok'
                            CHECK (status IN ('ok','timeout','error','refusal','fallback')),
    conversation_id         uuid,                          -- nullable: learning/batch have none
    request_id              text,                          -- Anthropic response id, for reconciliation
    batch                   boolean     NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_ai_calls_created_at ON ai_calls(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_calls_conversation ON ai_calls(conversation_id)
    WHERE conversation_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS ai_cost_daily (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    day                     date        NOT NULL,
    api_key_id              text        NOT NULL,
    model                   text        NOT NULL,
    billed_input_tokens     bigint      NOT NULL DEFAULT 0,
    billed_output_tokens    bigint      NOT NULL DEFAULT 0,
    billed_cost_usd         numeric(14,6) NOT NULL DEFAULT 0,
    ledger_cost_usd         numeric(14,6) NOT NULL DEFAULT 0,
    diff_usd                numeric(14,6) NOT NULL DEFAULT 0,
    created_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (day, api_key_id, model)
);

-- RLS — the API connects with the service_role key, which BYPASSES RLS.
-- Mirrors 022_blocklist.sql: explicit service_role policy, deny to everyone else.
ALTER TABLE public.ai_calls ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ai_calls_service_role_all ON public.ai_calls;
CREATE POLICY ai_calls_service_role_all
  ON public.ai_calls
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);

ALTER TABLE public.ai_cost_daily ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ai_cost_daily_service_role_all ON public.ai_cost_daily;
CREATE POLICY ai_cost_daily_service_role_all
  ON public.ai_cost_daily
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);
