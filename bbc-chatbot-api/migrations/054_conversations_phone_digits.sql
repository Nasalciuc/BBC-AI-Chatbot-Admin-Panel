-- 054 (part 4, 21 Nov 2026): the phone search compares digits. A phone is stored as the client typed it
-- ("+1 (214) 208-5185"); the panel's search box reads "2142085185" as a phone (app/services/chat_search.py)
-- and looks for its digits.
--
-- APPLIED: nu. The app works without it: until it exists, a phone search asks for the digits in a row with
-- only separators between them (visitor_phone ~ '2[^0-9]*1[^0-9]*4…'), the same answers, read row by row
-- (3,483 chats on 21 Nov). After a missing-column error the app re-probes every 60 s, so the first phone
-- search a minute after this runs uses it. Nothing else depends on it.
--
-- What it adds: a computed field, not a column. PostgREST calls visitor_phone_digits(conversations) when a
-- filter names it, and leaves it out of select=*: no row read with * starts carrying the phone a second
-- time (the detail's masking for supervisors and the blind take know only visitor_phone), no table rewrite,
-- and no write can collide with it. The planner inlines the SQL function, so the expression index below
-- serves '%2142085%' (checked on Postgres 16: Bitmap Index Scan on this index).
--
-- Two steps, each alone in the SQL editor (CONCURRENTLY cannot run in a transaction):
--   1. the function and its grants (instant);
--   2. the index, CONCURRENTLY: writes go on while it builds.
-- pg_trgm is in public on this project (an older install, checked 21 Nov), as 047 found it; the
-- search_path below finds the operator class wherever it is.

-- 1.
CREATE OR REPLACE FUNCTION public.visitor_phone_digits(public.conversations)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$ SELECT nullif(regexp_replace($1.visitor_phone, '[^0-9]', '', 'g'), '') $$;

-- The API reads it as service_role; nobody calls it as a function.
REVOKE EXECUTE ON FUNCTION public.visitor_phone_digits(public.conversations) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.visitor_phone_digits(public.conversations) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.visitor_phone_digits(public.conversations) TO service_role;

-- 2.
SET search_path = public, extensions;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversations_visitor_phone_digits_trgm
    ON public.conversations USING gin ((nullif(regexp_replace(visitor_phone, '[^0-9]', '', 'g'), '')) gin_trgm_ops);
