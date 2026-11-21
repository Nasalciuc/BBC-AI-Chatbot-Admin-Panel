-- 055 (part 4, 21 Nov 2026): the search box's "in the messages" (GET /api/search/messages) is an
-- ILIKE '%…%' on messages.content over the last 30 days. This index lets it read only the messages
-- that can match.
--
-- APPLIED: nu, and not needed today: 37,377 messages in all on 21 Nov (9.8 MB), about 5k a month, so the
-- search reads the month row by row in tens of milliseconds, a few times a minute at most (the endpoint's
-- own limit). The cost of the index is on every message written (a GIN entry per message, batched by
-- fastupdate), on the hottest insert of the app. Apply it when the search gets slow (p95 over ~200 ms)
-- or the table passes ~200k rows. Nothing depends on it.
--
-- CONCURRENTLY — run alone in the SQL editor, no BEGIN/COMMIT: writes go on while it builds.

SET search_path = public, extensions;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_content_trgm
    ON public.messages USING gin (content gin_trgm_ops);
