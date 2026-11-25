"""The visit registry, the API's side (contracts/live.md "The visit registry"; migration 056).

The owner, 25 Nov 2026: every visit (about 2,000 a day), with the visitor's address and the contacts we catch,
kept with no time limit for now, so the card's words can be judged per group of visitors on everything that
happened. bbc-live (Go) keeps each visit in Redis while it lasts; once it has been quiet for 30 minutes its key goes
to the queue `live:ended`. Every minute run_visit_registry takes up to BATCH of them into `site_visits`: one row a
visit, with its pages, where the visitor came from, their address and browser, the card and what came of it, and
the chat it started. Contacts are never copied: the view site_visits_full reads them from the chat and the lead.

VISIT_REGISTRY (app.flags, off at merge) makes POST /chat/live hand every visitor a pass for it, whatever the
live list's switch or the agent-first test's side. The job drains whatever is queued, on or off: a queue left by
a switch turned off still reaches the table. A table not there yet (056 not applied) leaves the queue as it is
(bbc-live keeps an ended visit 3 days), counted in /health visit_registry.table_missing.

One consumer at a time is not assumed: during a deploy two processes run the scheduler. A visit taken twice is
one row (UNIQUE visitor_id, started_at; the second insert is ignored), and each process removes from the queue only
the keys it stored (LREM), never a range, so neither drops a visit the other has not stored. A visit whose chat the
lookup could not find out about stays queued for the next run, LINK_TRIES runs at most, then goes in without it.

Nothing here logs an address, a name, an e-mail or a phone.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.flags import flag
from app.infra.redis_client import get_redis, redis_failed

logger = logging.getLogger(__name__)

ENDED_KEY = "live:ended"
BATCH = 200
_ENDED = re.compile(r"^live:ended:([A-Za-z0-9_-]{8,64}):(\d{9,11})(?::\d+)?$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_PAGE_ID = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STATES = {"shown", "dismissed", "engaged"}
# A chat begun this long before a visit's first snapshot is still the visit's (the widget asks for the pass first).
_CHAT_BEFORE_VISIT_S = 120
# And one begun up to this long after its last one (the visit's end is its last beat).
_CHAT_AFTER_VISIT_S = 1800
# A visit whose chat's lookup got no answer waits on the queue this many runs (a minute each) at most, counted in its
# own hash (bbc-live's ended key, deleted with it).
LINK_TRIES = 5
_TRIES_FIELD = "link_tries"
# Lookups at once (they share the database's thread pool with the chat).
_LOOKUPS_AT_ONCE = 4
# The visit's text fields as bbc-live writes them, and the table's names for them.
_TEXT = {
    "landing": "landing_page", "landing_type": "landing_type", "source": "source", "channel": "channel",
    "utm_source": "utm_source", "utm_medium": "utm_medium", "utm_campaign": "utm_campaign",
    "referrer": "referrer_host", "device": "device", "site": "site", "lang": "lang", "tz": "tz",
    "country": "country", "ua": "user_agent", "ph_session": "posthog_session_id", "card_trigger": "card_trigger",
    "card_variant": "card_variant", "card_surface": "card_surface", "card_answer": "card_answer",
    "last_page": "last_page", "from": "trip_from", "to": "trip_to", "cabin": "cabin",
}

VISIT_HEALTH: dict[str, Any] = {
    "runs": 0,            # runs that found something queued
    "rows": 0,            # visits stored
    "lost": 0,            # queued visits that ran out before the job took them (nothing to store)
    "bad_pages": 0,       # a page record that was not JSON (the page is left out, the visit is stored)
    "table_missing": 0,   # runs that found no table (056 not applied): the queue stays
    "store_failed": 0,    # runs the database refused or did not answer: the queue stays, the next run tries again
    "redis_failed": 0,    # runs that could not read or tidy the queue
    "linked": 0,          # chats found for a visit by the visitor's id (the widget didn't name the chat)
    "unlinked": 0,        # visits with a chat for which none began within the visit (stored without it)
    "link_failed": 0,     # chat lookups that got no answer (the visit waits on the queue for the next run)
    "link_given_up": 0,   # visits stored without their chat after LINK_TRIES runs with no answer
    "passes": 0,          # passes POST /chat/live handed for the registry alone (no live list for this visitor)
    "queued": None,       # the queue's length at the last run
}


def registry_enabled() -> bool:
    """POST /chat/live hands the widget a pass for the registry (live_visitors.widget_pass)."""
    from app.services import live_tokens

    return bool(flag("VISIT_REGISTRY", False)) and live_tokens.enabled()


def _int(v: Any) -> Optional[int]:
    """A whole number bbc-live wrote, or None."""
    s = str(v).strip() if v is not None else ""
    return int(s) if s.isdigit() else None


def _iso(unix: Any) -> Optional[str]:
    n = _int(unix)
    if not n:
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc).isoformat()


def _day(v: Any) -> Optional[str]:
    if not isinstance(v, str) or not _DAY.match(v):
        return None
    try:
        date.fromisoformat(v)
    except ValueError:  # noqa: silent — a date that is not a day (2026-02-30) is left out, as bbc-live would
        return None
    return v


def _ip(v: Any) -> Optional[str]:
    try:
        return str(ipaddress.ip_address(str(v).strip())) if v else None
    except ValueError:  # noqa: silent — an address that does not parse is left out of the row
        return None


def _small(v: Any, top: int) -> Optional[int]:
    n = _int(v)
    return n if n is not None and n <= top else None


def row_of(key: str, h: dict) -> Optional[dict]:
    """The table's row for one ended visit, or None when there is nothing to store (it ran out, or the key is not
    one of bbc-live's)."""
    m = _ENDED.match(key or "")
    first = _int((h or {}).get("first_at"))
    if not m or not first:
        return None
    last = max(_int(h.get("last_at")) or first, first)
    ids = [p for p in (h.get("pages") or "").split(",") if _PAGE_ID.match(p)]
    pages: list[dict] = []
    for pid in ids:
        raw = h.get(f"pg:{pid}")
        try:
            page = json.loads(raw) if raw else None
        except ValueError:  # noqa: silent — counted in bad_pages just below; the rest of the visit is stored
            page = None
        if not isinstance(page, dict):
            VISIT_HEALTH["bad_pages"] += 1
            continue
        pages.append({"id": pid, **page})
    row: dict[str, Any] = {
        "visitor_id": m.group(1),
        "started_at": _iso(first),
        "ended_at": _iso(last),
        "seconds_on_site": last - first,
        "seconds_seen": sum(min(int(p.get("seen") or 0), 86400) for p in pages if isinstance(p.get("seen"), int)),
        "pages": len(ids),
        "pages_over": _small(h.get("pages_over"), 32767) or 0,
        "ip": _ip(h.get("ip")),
        "depart_date": _day(h.get("depart")),
        "return_date": _day(h.get("return")),
        "pax": _small(h.get("pax"), 9),
        "card_state": h.get("card_state") if h.get("card_state") in _STATES else None,
        "card_at": _iso(h.get("card_at")),
        "card_answer_at": _iso(h.get("card_answer_at")),
        "has_chat": h.get("has_chat") == "1",
        "conversation_id": h.get("conversation_id") if _UUID.match(h.get("conversation_id") or "") else None,
        "events": [e for p in pages for e in (p.get("ev") or []) if isinstance(e, str)],
        "steps": [s for p in pages for s in (p.get("st") or []) if isinstance(s, str)],
        "page_list": pages,
    }
    for field, column in _TEXT.items():
        v = h.get(field)
        row[column] = v if isinstance(v, str) and v else None
    return row


def _needs_chat(row: dict) -> bool:
    """A visit with a chat the widget didn't name."""
    return bool(row["has_chat"] and not row["conversation_id"])


async def _first_chat(row: dict, gate: asyncio.Semaphore) -> Optional[dict]:
    from app.db import supabase as db

    # The window reaches as far back as a visit takes a chat, one begun just before its first snapshot (CodeRabbit
    # #282, round 3), and no further on than one begun within half an hour of its last.
    since = datetime.fromisoformat(row["started_at"]) - timedelta(seconds=_CHAT_BEFORE_VISIT_S)
    until = datetime.fromisoformat(row["ended_at"]) + timedelta(seconds=_CHAT_AFTER_VISIT_S)
    async with gate:
        return await db.first_chat_of_visit(row["visitor_id"], since.isoformat(), until.isoformat())


async def _link_chats(rows: list[dict]) -> set[int]:
    """A visit with a chat the widget didn't name: the visitor's first chat that began within the visit, looked up
    visit by visit (one bounded read each, CodeRabbit #282, round 7, second look). The indexes of the visits whose
    lookup got no answer: they wait on the queue for the next run instead of going in without their chat."""
    want = [i for i, r in enumerate(rows) if _needs_chat(r)]
    if not want:
        return set()
    gate = asyncio.Semaphore(_LOOKUPS_AT_ONCE)
    found = await asyncio.gather(*(_first_chat(rows[i], gate) for i in want))
    waiting: set[int] = set()
    for i, chat in zip(want, found):
        if chat is None:
            VISIT_HEALTH["link_failed"] += 1
            waiting.add(i)
        elif chat:
            rows[i]["conversation_id"] = chat["id"]
            VISIT_HEALTH["linked"] += 1
        else:
            VISIT_HEALTH["unlinked"] += 1
    return waiting


async def run_visit_registry() -> dict:
    """The scheduler's job (every minute): the queue into site_visits. A result that says what it did."""
    from app.db import supabase as db

    r = get_redis()
    if r is None:
        return {"state": "no_redis"}
    try:
        keys = await r.lrange(ENDED_KEY, 0, BATCH - 1)
        if not keys:
            VISIT_HEALTH["queued"] = 0
            return {"state": "empty"}
        pipe = r.pipeline(transaction=False)
        for k in keys:
            pipe.hgetall(k)
        hashes = await pipe.execute()
        VISIT_HEALTH["queued"] = await r.llen(ENDED_KEY)
    except Exception as e:
        redis_failed("visit registry read", e)
        VISIT_HEALTH["redis_failed"] += 1
        logger.error(f"[visit-registry] the queue could not be read: {type(e).__name__}: {e}")
        return {"state": "redis_failed"}
    VISIT_HEALTH["runs"] += 1
    taken: list[tuple[str, dict, int]] = []   # (key, row, the runs its chat's lookup already failed)
    lost = 0
    for k, h in zip(keys, hashes):
        row = row_of(k, h or {})
        if row is None:
            lost += 1
        else:
            taken.append((k, row, _int((h or {}).get(_TRIES_FIELD)) or 0))
    rows = [row for _, row, _ in taken]
    waiting: set[int] = set()
    try:
        waiting = await _link_chats(rows)
    except Exception as e:
        logger.error(f"[visit-registry] chats not looked up: {type(e).__name__}: {e}")
        waiting = {i for i, row in enumerate(rows) if _needs_chat(row)}
        VISIT_HEALTH["link_failed"] += len(waiting)
    # A visit whose chat got no answer waits on the queue for the next run, LINK_TRIES runs at most: past them it goes
    # in without its chat, so a lookup that never answers doesn't hold the queue (CodeRabbit #282, round 7).
    kept: set[str] = set()
    for i in waiting:
        k, _, tries = taken[i]
        if tries + 1 < LINK_TRIES:
            kept.add(k)
        else:
            VISIT_HEALTH["link_given_up"] += 1
            logger.warning(f"[visit-registry] a visit's chat got no answer in {LINK_TRIES} runs: stored without it")
    stored = [row for k, row, _ in taken if k not in kept]
    state = await db.store_site_visits(stored) if stored else "stored"
    if state == "table_missing":
        VISIT_HEALTH["table_missing"] += 1
        return {"state": "table_missing", "queued": VISIT_HEALTH["queued"]}
    if state != "stored":
        VISIT_HEALTH["store_failed"] += 1
        return {"state": "store_failed", "queued": VISIT_HEALTH["queued"]}
    try:
        pipe = r.pipeline(transaction=False)
        for k in keys:
            if k in kept:
                pipe.hincrby(k, _TRIES_FIELD, 1)
                continue
            pipe.lrem(ENDED_KEY, 1, k)
            pipe.delete(k)
        await pipe.execute()
    except Exception as e:
        # Stored, not tidied: the next run takes them again and the table ignores them (one row a visit).
        redis_failed("visit registry tidy", e)
        VISIT_HEALTH["redis_failed"] += 1
        logger.error(f"[visit-registry] stored but not taken off the queue: {type(e).__name__}: {e}")
    VISIT_HEALTH["rows"] += len(stored)
    VISIT_HEALTH["lost"] += lost
    return {"state": "stored", "rows": len(stored), "lost": lost, "waiting": len(kept),
            "queued": VISIT_HEALTH["queued"]}
