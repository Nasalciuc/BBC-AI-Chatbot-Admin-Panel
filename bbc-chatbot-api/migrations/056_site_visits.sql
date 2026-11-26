-- 056 (25 Nov 2026): the visit registry (contracts/live.md "The visit registry"; app/services/visit_registry.py).
-- The owner, 25 Nov: every visit, about 2,000 a day, with the visitor's address and the contacts we catch, kept with
-- no time limit for now, so the cards' words can be judged per group of visitors on everything that happened.
--
-- APPLIED: nu. The registry is off at merge (VISIT_REGISTRY in app_settings.flags): the widget gets no pass for it
-- and bbc-live queues nothing. Turn it on only after this runs; before it, the API's job leaves the queue in Redis
-- (3 days) and counts every try in /health (visit_registry.table_missing).
--
-- What it adds, nothing it rewrites:
--   site_visits         one row a visit (bbc-live's visit, quiet for 30 minutes). No name, e-mail or phone in it:
--                       the address (ip) and the browser (user_agent) are the only things that point at a person.
--   site_visits_full    each visit with what came of it: the chat, its contacts, the lead, the CRM. Contacts are read
--                       from the chat and the lead, never copied.
--   site_visits_daily   per New York day, per group (source, screen, came back) and per card (moment, words): the
--                       counts the panel's table reads.
-- RLS on, nothing granted to anon or authenticated: the API (service role) is the only reader. The views run with the
-- caller's rights (security_invoker), so they never open the table to anyone it is closed to.
-- lock_timeout: the views read conversations and leads; creating them waits at most 5 s for a lock.

BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE TABLE IF NOT EXISTS public.site_visits (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    visitor_id         text        NOT NULL,
    started_at         timestamptz NOT NULL,
    ended_at           timestamptz NOT NULL,
    seconds_on_site    integer     NOT NULL DEFAULT 0,
    seconds_seen       integer     NOT NULL DEFAULT 0,
    pages              smallint    NOT NULL DEFAULT 0,
    pages_over         smallint    NOT NULL DEFAULT 0,
    landing_page       text,
    landing_type       text,
    last_page          text,
    site               text,
    device             text,
    source             text,
    channel            text,
    utm_source         text,
    utm_medium         text,
    utm_campaign       text,
    referrer_host      text,
    country            text,
    lang               text,
    tz                 text,
    ip                 inet,
    user_agent         text,
    trip_from          text,
    trip_to            text,
    depart_date        date,
    return_date        date,
    cabin              text,
    pax                smallint,
    card_trigger       text,
    card_variant       text,
    card_surface       text,
    card_state         text,
    card_answer        text,
    card_at            timestamptz,
    card_answer_at     timestamptz,
    has_chat           boolean     NOT NULL DEFAULT false,
    conversation_id    uuid,
    posthog_session_id text,
    events             text[]      NOT NULL DEFAULT '{}',
    steps              text[]      NOT NULL DEFAULT '{}',
    page_list          jsonb       NOT NULL DEFAULT '[]'::jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    -- One row a visit: the job may take a visit twice (a deploy runs two schedulers for a minute); the second is
    -- ignored (ON CONFLICT DO NOTHING).
    CONSTRAINT site_visits_once UNIQUE (visitor_id, started_at),
    CONSTRAINT site_visits_card_state CHECK (card_state IS NULL OR card_state IN ('shown', 'dismissed', 'engaged'))
);

CREATE INDEX IF NOT EXISTS idx_site_visits_started ON public.site_visits (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_site_visits_visitor ON public.site_visits (visitor_id, started_at);
CREATE INDEX IF NOT EXISTS idx_site_visits_conversation ON public.site_visits (conversation_id)
    WHERE conversation_id IS NOT NULL;

ALTER TABLE public.site_visits ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.site_visits FROM anon, authenticated;

CREATE OR REPLACE VIEW public.site_visits_full WITH (security_invoker = true) AS
SELECT v.*,
       EXISTS (SELECT 1 FROM public.site_visits p
               WHERE p.visitor_id = v.visitor_id AND p.started_at < v.started_at) AS returning,
       c.visitor_name,
       c.visitor_email,
       c.visitor_phone,
       c.status            AS chat_status,
       c.mode              AS chat_mode,
       c.assigned_agent_id,
       l.id                AS lead_id,
       l.tier              AS lead_tier,
       l.score             AS lead_score,
       COALESCE(l.created_in_crm, false) AS in_crm,
       l.crm_lead_id
FROM public.site_visits v
LEFT JOIN public.conversations c ON c.id = v.conversation_id
LEFT JOIN public.leads l ON l.conversation_id = v.conversation_id;

REVOKE ALL ON public.site_visits_full FROM anon, authenticated;

CREATE OR REPLACE VIEW public.site_visits_daily WITH (security_invoker = true) AS
SELECT (f.started_at AT TIME ZONE 'America/New_York')::date                AS day,
       COALESCE(NULLIF(f.source, ''), 'site')                              AS source,
       COALESCE(NULLIF(f.channel, ''), '')                                 AS channel,
       COALESCE(NULLIF(f.device, ''), '')                                  AS device,
       f.returning,
       COALESCE(NULLIF(f.card_trigger, ''), '')                            AS card_trigger,
       COALESCE(NULLIF(f.card_variant, ''), '')                            AS card_variant,
       count(*)                                                            AS visits,
       count(*) FILTER (WHERE f.card_state IS NOT NULL)                    AS cards,
       count(*) FILTER (WHERE f.card_state = 'engaged')                    AS engaged,
       count(*) FILTER (WHERE f.card_state = 'dismissed')                  AS dismissed,
       count(*) FILTER (WHERE f.has_chat)                                  AS chats,
       count(*) FILTER (WHERE COALESCE(f.visitor_email, '') <> ''
                           OR COALESCE(f.visitor_phone, '') <> '')         AS contacts,
       count(*) FILTER (WHERE f.lead_id IS NOT NULL)                       AS leads,
       count(*) FILTER (WHERE f.in_crm)                                    AS in_crm,
       sum(f.seconds_on_site)                                              AS seconds_on_site
FROM public.site_visits_full f
GROUP BY 1, 2, 3, 4, 5, 6, 7;

REVOKE ALL ON public.site_visits_daily FROM anon, authenticated;

COMMIT;
