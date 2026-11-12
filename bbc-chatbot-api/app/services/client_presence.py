"""The CLIENT's presence on the site (the visitor, not the agent: that is
services/presence.py): one rule, read by everything.

The list, the open chat, the live stream, the "client left" sweep, routing
and the no-agent alert all ask derive_effective_presence. Pure: no database,
no settings, so the services that only shape numbers (agent_day) can use it
too. app.db.supabase re-exports it for the callers that already import it
from there.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


# A stored "online" is a CLAIM, not a fact: the widget's close beacon is
# best-effort (a killed tab, a suspended phone, a dropped network all lose
# it), so 9 of the 34 vanish-census conversations sat at `online` forever
# with the client long gone. Presence is DERIVED on read, from the age of
# the last signal — the widget pings every 30s while it is really open.
PRESENCE_STALE_SECONDS = 90      # two missed pings
PRESENCE_LEFT_SECONDS = 1800     # 30 min: a backgrounded tab is quiet, not gone

# ── Presence v2 (widget signals with pres_v = 2) ──────────────────────────
# The first rule only heard from the widget while the chat was OPEN, and took
# "left" from a beacon that a killed tab or a phone put to sleep never sends.
# 30 days to 12 Nov 2026: of 581 chats, 124 ended with the beacon's "left",
# 171 sat at "online" and 263 at "minimized", whatever the client really did.
#
# The v2 widget sends a beat every 30 s while the PAGE is visible, chat open
# or closed, with the seconds since the visitor last moved, scrolled, typed or
# touched the page; a `hidden` signal when the page goes out of view (the last
# event a page reliably gets, on a phone above all); a `leaving` signal on
# pagehide. "Left" is never taken from a signal alone: it is the silence that
# follows one. Thresholds as other chat products use them (Zendesk: a phone
# browser in the background ends the session in 20 s to 2 min, 20 min idle
# ends it; tawk.to: 20 min of inactivity; Comm100: out of site after ~3 min).
PRESENCE_IDLE_SECONDS = 600            # no activity on the page: "inactive"
PRESENCE_IDLE_LEFT_SECONDS = 1200      # 20 min without activity: gone
PRESENCE_LEAVE_GRACE_SECONDS = 90      # pagehide, and no beat from a next page
PRESENCE_PHONE_AWAY_SECONDS = 120      # a phone's browser in the background
PRESENCE_SILENT_LEFT_SECONDS = 600     # nothing at all: the safety net
# The "client left" sweep closes a chat once the client has been gone this
# long, counted from the last moment they were there (as before: 5 minutes).
PRESENCE_CLOSE_AFTER_SECONDS = 300

# The metadata keys a v2 signal writes (signal_patch below, written by chat.py ping_chat_session).
PRESENCE_V2_KEYS = (
    "pres_v", "pres_seen_at", "pres_active_at", "pres_chat_open",
    "pres_mobile", "pres_hidden_at", "pres_leaving_at",
)


def _presence_ts(value) -> Optional[datetime]:
    """An ISO timestamp as an aware datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):  # noqa: silent — an unreadable timestamp is "no signal at that time": the rule then reads the other signals, or says nothing; nothing is written or sent
        return None
    # Legacy rows stored naive timestamps; subtracting one from an
    # aware `now` raises TypeError and blanked the whole chats list.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _seconds(now_dt: datetime, then: datetime) -> int:
    # A clock skew that puts a signal in the future must not read as
    # "seen -20s ago" and certainly not as stale.
    return max(0, int((now_dt - then).total_seconds()))


def _is_true(value) -> bool:
    """jsonb ->> hands the list the STRING "true"; the detail has a bool."""
    return value is True or value == "true"


def _derive_presence_v2(
    meta: dict, now_dt: datetime, typed: Optional[datetime]
) -> tuple[Optional[str], Optional[int]]:
    """(state, seconds) from v2 signals. States: online (on the site, chat
    open), minimized (on the site, chat closed), idle (on the site, nothing
    touched for 10 min), away (page out of view: another tab or app),
    stale (no signal, no reason given), left.

    The seconds belong to the state: since the last signal for online /
    minimized / stale, since the last activity for idle, since the page went
    out of view for away, and for left since the last moment the client was
    there — so "Left 12 min ago" and the sweep's 5 minutes count from the
    same instant."""
    # A message is a beat and an activity: the client is typing in the chat.
    seen = max(filter(None, (_presence_ts(meta.get("pres_seen_at")), typed)), default=None)
    active = max(filter(None, (_presence_ts(meta.get("pres_active_at")), typed)), default=None)
    hidden = _presence_ts(meta.get("pres_hidden_at"))
    leaving = _presence_ts(meta.get("pres_leaving_at"))
    # A signal counts while no beat came after it: the next page's first beat,
    # or the page coming back into view, cancels it. A beat stamped the same
    # instant cancels it too: the next page can beat within the millisecond.
    if leaving and seen and leaving <= seen:
        leaving = None
    if hidden and seen and hidden <= seen:
        hidden = None
    if not (seen or hidden or leaving):
        return None, None

    at = now_dt
    if leaving:
        gone = _seconds(now_dt, leaving)
        if gone >= PRESENCE_LEAVE_GRACE_SECONDS:
            return "left", gone
        # A page change sends a beat from the next page within seconds:
        # until the grace runs out, show what was true when the page closed.
        at = leaving
        hidden = None
    if hidden:
        out_of_view = _seconds(now_dt, hidden)
        if _is_true(meta.get("pres_mobile")) and out_of_view >= PRESENCE_PHONE_AWAY_SECONDS:
            return "left", out_of_view
        quiet = _seconds(now_dt, active or hidden)
        if quiet >= PRESENCE_IDLE_LEFT_SECONDS:
            return "left", quiet
        return "away", out_of_view
    if seen is None:
        return None, None
    silent = _seconds(at, seen)
    quiet = _seconds(at, active) if active else silent
    if quiet >= PRESENCE_IDLE_LEFT_SECONDS:
        return "left", _seconds(now_dt, active) if active else _seconds(now_dt, seen)
    if silent >= PRESENCE_SILENT_LEFT_SECONDS:
        return "left", _seconds(now_dt, seen)
    if silent >= PRESENCE_STALE_SECONDS:
        return "stale", silent
    if quiet >= PRESENCE_IDLE_SECONDS:
        return "idle", quiet
    return ("online" if _is_true(meta.get("pres_chat_open")) else "minimized"), silent


def derive_effective_presence(
    metadata: dict,
    now: Optional[datetime] = None,
    last_user_message_at: Optional[str] = None,
) -> tuple[Optional[str], Optional[int]]:
    """(effective_presence, age_seconds): the ONE rule the list, the open
    chat, the live stream, the "client left" sweep, routing and the
    no-agent alert all read.

    Conversations whose widget sends v2 signals (pres_v = 2) go through
    _derive_presence_v2. The rest keep the first rule below, which never
    upgrades a state — only ages one down: online → stale → left.

    Two guards learned the hard way:
      * Only conversations whose widget ACTUALLY pings are aged down
        (`widget_pings`). Without it, every client running a cached older
        bundle would read "left" the moment this deploys — a fleet-wide
        lie dressed as a fix.
      * A client who is actively SENDING messages is present, whatever
        the ping stream says: the message path only rewrites presence
        when it changes, so a chatty client's timestamp can be minutes
        old while they are mid-sentence.
    """
    meta = metadata or {}
    now_dt = now or datetime.now(timezone.utc)
    typed = _presence_ts(last_user_message_at)
    if str(meta.get("pres_v")) == "2":
        return _derive_presence_v2(meta, now_dt, typed)

    stored = meta.get("widget_presence")
    raw_at = meta.get("widget_last_event_at")
    if not raw_at:
        return stored, None
    seen = _presence_ts(raw_at)
    if seen is None:
        return stored, None
    age = _seconds(now_dt, seen)
    if stored != "online":
        return stored, age
    if not meta.get("widget_pings"):
        return stored, age          # no heartbeat to reason from
    if typed is not None:
        age = min(age, _seconds(now_dt, typed))
    if age >= PRESENCE_LEFT_SECONDS:
        return "left", age
    if age >= PRESENCE_STALE_SECONDS:
        return "stale", age
    return "online", age


def presence_changes_in(
    metadata: dict,
    now: Optional[datetime] = None,
    last_user_message_at: Optional[str] = None,
) -> Optional[int]:
    """Seconds until the rule's answer changes BY ITSELF, if no new signal
    comes; None when it never would. "No signal" and "left" are made of
    silence, which nothing pushes: the panel reads the presence again at
    this moment instead of polling for it (the panel's polls stop while its
    live stream is open, PR-10).

    The answer can only change when a signal's age crosses a threshold, so
    those instants are the only ones worth asking about."""
    meta = metadata or {}
    now_dt = now or datetime.now(timezone.utc)
    state, _age = derive_effective_presence(meta, now_dt, last_user_message_at)
    typed = _presence_ts(last_user_message_at)
    if str(meta.get("pres_v")) == "2":
        stamps = [_presence_ts(meta.get(k)) for k in (
            "pres_seen_at", "pres_active_at", "pres_hidden_at", "pres_leaving_at")]
        thresholds = (
            PRESENCE_STALE_SECONDS, PRESENCE_LEAVE_GRACE_SECONDS, PRESENCE_PHONE_AWAY_SECONDS,
            PRESENCE_IDLE_SECONDS, PRESENCE_IDLE_LEFT_SECONDS, PRESENCE_SILENT_LEFT_SECONDS,
        )
    else:
        stamps = [_presence_ts(meta.get("widget_last_event_at"))]
        thresholds = (PRESENCE_STALE_SECONDS, PRESENCE_LEFT_SECONDS)
    stamps.append(typed)
    # +1: thresholds are reached AT the second; a whole second later the
    # answer has certainly moved.
    candidates = sorted({
        int((ts - now_dt).total_seconds()) + th + 1
        for ts in stamps if ts is not None
        for th in thresholds
    })
    for wait in candidates:
        if wait <= 0:
            continue
        later = now_dt + timedelta(seconds=wait)
        if derive_effective_presence(meta, later, last_user_message_at)[0] != state:
            return wait
    return None


def client_left(
    metadata: dict,
    last_user_message_at: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """Seconds since the client was last there when the rule says they
    left; None when they did not (or nothing is known)."""
    state, age = derive_effective_presence(metadata, now, last_user_message_at)
    if state != "left":
        return None
    return age if age is not None else 0


# The longest "seconds since the visitor last touched the page" a signal may
# claim: a day. Anything larger is a wrong clock, not a longer absence.
_IDLE_MAX_SECONDS = 86_400


def signal_patch(
    kind: str, now: datetime, chat: Optional[str], idle: Optional[int], m: Optional[int]
) -> dict:
    """The metadata keys one v2 widget signal writes (PRESENCE_V2_KEYS).

    beat: the page is open and in view (every 30 s, chat open or closed).
    hidden: the page went out of view (another tab or app, a locked phone).
    leaving: pagehide — the page closed or the visitor went to another page.
    The rule that reads them is derive_effective_presence, below."""
    now_iso = now.isoformat()
    patch: dict = {"pres_v": 2}
    if idle is not None:
        quiet = min(max(int(idle), 0), _IDLE_MAX_SECONDS)
        patch["pres_active_at"] = (now - timedelta(seconds=quiet)).isoformat()
    elif kind == "beat":
        patch["pres_active_at"] = now_iso
    if m is not None:
        patch["pres_mobile"] = m == 1
    if kind == "hidden":
        patch["pres_hidden_at"] = now_iso
    elif kind == "leaving":
        patch["pres_leaving_at"] = now_iso
        # The first rule's keys say the same, for an older tab of this visitor
        # and for anything that still reads them.
        patch.update({
            "widget_open": False,
            "widget_presence": "left",
            "widget_last_event": "close",
            "widget_last_close_reason": "left",
            "widget_last_event_at": now_iso,
        })
    else:
        chat_open = chat != "closed"
        patch.update({
            "pres_seen_at": now_iso,
            "pres_chat_open": chat_open,
            "widget_pings": True,
            "widget_open": chat_open,
            "widget_presence": "online" if chat_open else "minimized",
            "widget_last_event": "ping",
            "widget_last_event_at": now_iso,
        })
    return patch
