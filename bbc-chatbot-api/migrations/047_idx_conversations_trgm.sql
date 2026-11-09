-- 047 (PR-14): the panel's search (ILIKE '%…%' on name, email, phone) can
-- use an index instead of reading every conversation.
--
-- CONCURRENTLY — no BEGIN/COMMIT on purpose; run each statement alone in the
-- SQL editor. pg_trgm goes in the extensions schema when it is new; where an
-- older install put it in public, that one is used — the operator class is
-- found through the search_path either way.
--
-- APPLIED: nu.
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;
SET search_path = public, extensions;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversations_visitor_name_trgm
    ON public.conversations USING gin (visitor_name gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversations_visitor_email_trgm
    ON public.conversations USING gin (visitor_email gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversations_visitor_phone_trgm
    ON public.conversations USING gin (visitor_phone gin_trgm_ops);
