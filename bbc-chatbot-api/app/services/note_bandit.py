"""How the tapped note asks its first question when the page named no destination (owner, 17 Nov 2026:
"altă primă întrebare … testată ca bandit").

From 16 Nov the tapped note opens the chat with no form. None of the 12 note chats by 17 Nov left an
email or a phone: with no destination on the page, the first question was where and when, typed, and
7 of the 7 the bot carried alone stopped right there. Two ways to ask it, one per chat:

- ``open``: as before, "Tell me where and roughly when", typed (proactive.compose).
- ``places``: "Where are you thinking of flying?", with buttons for where most leads go (Europe and
  Asia: 598 of the 615 leads to the 15 most asked-for destinations, Jul-Nov 2026) and "Somewhere else".

Which way a chat gets is a bandit's choice (Thompson sampling) that learns from every chat: the reward
is the visitor's second message — they answered Alex after their first one. Until each way has
MIN_CHATS chats, each is picked half the time; then each chat goes to the way whose draw from
Beta(1 + continued, 1 + stopped) is higher, so the better way gets more chats while the other is
still tried.

The counts come from the conversations themselves (``metadata.proactive_variant``, written by
proactive.open_conversation from the variant the widget sends back with the answer), read in the
background at most every STATS_TTL_S: /offer never waits for the database and never writes. The
widget also sends the variant with proactive_shown, so PostHog has the share that started a chat.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

VARIANTS: tuple[str, ...] = ("open", "places")
# "places": where most leads go, then anything else typed; "I have a question" as on every note.
PLACES_REPLIES: tuple[str, ...] = ("Europe", "Asia", "Somewhere else", "I have a question")

# Half and half until each way has this many chats.
MIN_CHATS = 20
STATS_TTL_S = 600
WINDOW_DAYS = 30
_MAX_CHATS = 1000
_IDS_PER_READ = 100

# {variant: (chats, continued)} as last read, when it was read (monotonic s), and the read in flight.
_STATS: dict = {"counts": None, "at": None, "task": None}


def known(variant: object) -> Optional[str]:
    """The variant when it is one of ours; anything else (a bad value, an older or newer widget) is None."""
    return variant if isinstance(variant, str) and variant in VARIANTS else None


def choose(counts: Optional[dict] = None, rng: Optional[random.Random] = None) -> str:
    """One chat's way. `counts`: {variant: (chats, continued)}; None reads the cached ones."""
    rng = rng or random
    counts = _STATS["counts"] if counts is None else counts
    stats = {v: _pair(counts.get(v) if isinstance(counts, dict) else None) for v in VARIANTS}
    if any(chats < MIN_CHATS for chats, _ in stats.values()):
        return rng.choice(VARIANTS)
    draws = {v: rng.betavariate(1 + won, 1 + chats - won) for v, (chats, won) in stats.items()}
    return max(VARIANTS, key=lambda v: draws[v])


def _pair(value: object) -> tuple[int, int]:
    """(chats, continued) from a stored count; anything malformed is (0, 0)."""
    try:
        chats, won = value  # type: ignore[misc]
        chats, won = int(chats), int(won)
    except (TypeError, ValueError):  # noqa: silent — a count that is not one is "no chats yet": that way is then picked half the time, as at the start; the visitor gets one of the two openings either way, nothing is written
        return (0, 0)
    chats = max(0, chats)
    return (chats, min(max(0, won), chats))


def counts() -> Optional[dict]:
    """The counts as last read (for the health counters and the tests); None before the first read."""
    return _STATS["counts"]


def refresh_soon() -> None:
    """Read the counts again in the background when they are older than STATS_TTL_S (or never read).
    Never waits and never raises: /offer answers with what is cached (none yet: half and half)."""
    at = _STATS["at"]
    if at is not None and time.monotonic() - at < STATS_TTL_S:
        return
    task = _STATS["task"]
    if task is not None and not task.done():
        return
    try:
        _STATS["task"] = asyncio.get_running_loop().create_task(_refresh())
    except RuntimeError:  # noqa: silent — no running loop (a sync caller): the counts are read by the next request that has one; the visitor's opening comes from the last counts meanwhile
        return


async def _refresh() -> None:
    try:
        fresh = await _read_counts()
    except Exception as e:
        # A database that fails is not asked again before the TTL; meanwhile the last counts stand.
        _STATS["at"] = time.monotonic()
        logger.warning(f"[note-bandit] counts not read ({type(e).__name__}): the last ones stand")
        return
    _STATS["counts"] = fresh
    _STATS["at"] = time.monotonic()
    logger.info(f"[note-bandit] chats and second messages per way: {fresh}")


async def _read_counts() -> dict:
    """{variant: (chats, continued)} over the note chats of the last WINDOW_DAYS that carry a variant.
    Two reads: the chats, then their visitors' messages (only how many each sent, never what)."""
    from app.db import supabase as db

    client = db.get_client()
    since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()
    res = await db._run_sync(
        lambda: client.table("conversations")
        .select("id, proactive_variant:metadata->>proactive_variant")
        .eq("metadata->>proactive_trigger", "note")
        .not_.is_("metadata->>proactive_variant", "null")
        .gte("created_at", since)
        .order("created_at", desc=True)
        .limit(_MAX_CHATS)
        .execute()
    )
    way_of: dict[str, str] = {}
    for row in res.data or []:
        way = known(row.get("proactive_variant")) if isinstance(row, dict) else None
        if way and isinstance(row.get("id"), str):
            way_of[row["id"]] = way
    sent: dict[str, int] = {cid: 0 for cid in way_of}
    ids = list(way_of)
    for i in range(0, len(ids), _IDS_PER_READ):
        chunk = ids[i:i + _IDS_PER_READ]
        res = await db._run_sync(
            lambda chunk=chunk: client.table("messages")
            .select("conversation_id")
            .in_("conversation_id", chunk)
            .eq("role", "user")
            .limit(_IDS_PER_READ * 50)
            .execute()
        )
        for row in res.data or []:
            cid = row.get("conversation_id") if isinstance(row, dict) else None
            if cid in sent:
                sent[cid] += 1
    out = {v: [0, 0] for v in VARIANTS}
    for cid, way in way_of.items():
        out[way][0] += 1
        if sent[cid] >= 2:
            out[way][1] += 1
    return {v: (chats, won) for v, (chats, won) in out.items()}


def _reset_for_tests() -> None:
    _STATS.update({"counts": None, "at": None, "task": None})
