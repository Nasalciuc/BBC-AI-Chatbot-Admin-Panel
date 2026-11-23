"""Allowlist for widget-supplied conversation metadata (PR-4).

The widget may set only attribution/landing fields. Everything that affects
routing, identity or billing — client_ip, site, engaged_agent_id,
sticky_agent_id and the pipeline's own state keys — is written by the server
only. Before PR-4 the client's values were merged first and server values
added with setdefault, so a client could pin its own client_ip or
engaged_agent_id. This drops anything outside the allowlist at ingress.
"""

from __future__ import annotations

import re

# Exact keys the widget may send: what bbc-widget/src/utm.ts captures (the
# page, the referrer, every platform's click id, the Analytics client id) and
# the visitor id. The server reads each of them: the CRM's utm block takes
# google_analytics_client_id and kayak_click_id, the source fallback reads
# msclkid (bing) and kayak_click_id / kclid. Dropping them at ingress lost
# that attribution without a word.
_ALLOWED_EXACT = frozenset({
    "page_url", "page_path", "referrer", "visitor_id",
    "gclid", "fbclid", "msclkid", "ttclid", "kayak_click_id", "kclid", "skyscanner_click_id",
    "google_analytics_client_id",
})
# Prefixes the widget may send (utm_source, landing_city, …).
_ALLOWED_PREFIXES = ("utm_", "landing_")
# Every allowed value is a string the widget took from the page. Anything else
# (an object, a list, a megabyte) is not attribution and never reaches the
# conversation row, the panel or the CRM.
# "en-AU", "es-419", "pt-BR"; "Australia/Adelaide", "America/Argentina/Buenos_Aires".
_BROWSER_LANG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,4}){0,2}")
_TIME_ZONE = re.compile(r"[A-Za-z]+(?:/[A-Za-z0-9_+\-]+){1,2}")
_MAX_KEY = 64
_MAX_VALUE = 2048  # a page URL or a referrer can be long; nothing else comes close
_MAX_KEYS = 50     # attribution keys; the validated keys below come on top
_POSTHOG_ID = re.compile(r"[0-9A-Za-z-]{16,64}")
_SOURCE_VIA = frozenset({"posthog", "direct"})

# What the site says the page is (bbc-signals.astro, window.bbcPage.page_type): one word of
# bbc-widget/src/siteSignals.ts PAGE_TYPES, the same list.
_PAGE_TYPES = frozenset({
    "home", "route", "destination", "origin", "airline", "deals", "search",
    "flight", "quote", "thank_you", "blog", "about", "legal", "other",
})

# What the visitor did on the site before the chat (siteSignals.ts activityLine): up to 12 tokens
# joined by ';', at most 300 characters, each one of the widget's fixed shapes (activityToken). The
# shapes hold codes, dates, small numbers and fixed words only, so nothing the visitor typed can
# ride the line; a single token outside them drops the whole line.
_FIELD_KIND = (
    r"(?:name|email|phone|origin|destination|dates|passengers|cabin|trip_type|message"
    r"|consent|captcha|other)"
)
_ACTIVITY_TOKEN = re.compile(
    r"search(?::(?:[A-Z]{3}-(?:[A-Z]{3}|\?)|\?-[A-Z]{3}))?(?::[0-9]{4}-[0-9]{2}-[0-9]{2})?"
    r"|quote_opened"
    r"|quote_step(?::(?:[1-9]|1[0-9]|20))?"
    rf"|quote_invalid(?::{_FIELD_KIND}(?:\|{_FIELD_KIND}){{0,5}})?"
    r"|quote_submitting"
    r"|quote_failed:(?:server|network|captcha|other)"
    r"|quote_submitted"
    r"|contact(?::(?:phone|whatsapp|email))?"
)
_MAX_ACTIVITY = 300
_MAX_ACTIVITY_TOKENS = 12


def page_activity_tokens(value: object) -> list[str] | None:
    """The tokens of a page_activity line, or None when the line is not the widget's.

    Shared by the allowlist below and the prompt (app.ai.prompts), so the prompt
    only ever reads tokens that passed this check.
    """
    if not isinstance(value, str) or not value or len(value) > _MAX_ACTIVITY:
        return None
    tokens = value.split(";")
    if len(tokens) > _MAX_ACTIVITY_TOKENS:
        return None
    if not all(_ACTIVITY_TOKEN.fullmatch(t) for t in tokens):
        return None
    return tokens


def _known_sites() -> set[str]:
    from app.ai.prompts import SITE_CONFIGS
    return set(SITE_CONFIGS)


def allowed_widget_metadata(raw: dict | None) -> dict:
    """Keep only the attribution/landing fields; drop everything else.

    Some widget values are needed and validated rather than dropped:
    - `site` — which brand the chat belongs to (bbc | bct). Nothing on the
      server derives it; dropping it made every businessclass-tickets chat a
      BBC chat (prompts, phone, closing, CRM base). Kept only when it names a
      configured brand.
    - `booking_id` — the support tunnel's ticket number, a short string.
    - `page_type` / `page_activity` — what the site says the page is and what
      the visitor did on it before the chat (bbc-signals), for the prompt.
      Kept only in the widget's own vocabulary (_PAGE_TYPES, _ACTIVITY_TOKEN).
    """
    if not raw:
        return {}
    out: dict = {}
    for k, v in raw.items():
        if len(out) >= _MAX_KEYS:
            break
        key = str(k)
        if len(key) > _MAX_KEY or not isinstance(v, str):
            continue
        if key in _ALLOWED_EXACT or key.startswith(_ALLOWED_PREFIXES):
            out[key] = v[:_MAX_VALUE]
    site = raw.get("site")
    if isinstance(site, str) and site in _known_sites():
        out["site"] = site
    booking = raw.get("booking_id")
    if isinstance(booking, str) and 0 < len(booking.strip()) <= 64:
        out["booking_id"] = booking.strip()
    # The PostHog session of the visit, so the panel can link the agent to the
    # replay of what the client did before writing. An id, nothing else.
    ph = raw.get("posthog_session_id")
    if isinstance(ph, str) and _POSTHOG_ID.fullmatch(ph.strip()):
        out["posthog_session_id"] = ph.strip()
    page_type = raw.get("page_type")
    if isinstance(page_type, str) and page_type in _PAGE_TYPES:
        out["page_type"] = page_type
    activity = raw.get("page_activity")
    if page_activity_tokens(activity):
        out["page_activity"] = activity
    # Where the client probably is, for a phone typed without its country code
    # (app.services.phone_country): the browser's language tag and time zone.
    lang = raw.get("browser_lang")
    if isinstance(lang, str) and _BROWSER_LANG.fullmatch(lang.strip()):
        out["browser_lang"] = lang.strip()
    tz = raw.get("tz")
    if isinstance(tz, str) and _TIME_ZONE.fullmatch(tz.strip()):
        out["tz"] = tz.strip()
    # Where the visit came from when the tab kept nothing of it (bbc-widget utm.ts sessionSource, 23 Nov): "posthog"
    # when the attribution keys beside it came from the visit's PostHog session, "direct" when PostHog saw no site
    # before the visit. One of the two words, nothing else.
    via = raw.get("source_via")
    if isinstance(via, str) and via in _SOURCE_VIA:
        out["source_via"] = via
    return out
