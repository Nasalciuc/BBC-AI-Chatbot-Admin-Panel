"""An agent takes a sales chat once Alex has the client's contact, the client asked for a person, or the hold ran out.

The owner's decision of 25 Nov 2026 ("Mai multe chaturi și leaduri", lever 2). On 17-24 Nov, in 98 chats an agent
wrote before the client had left a phone or an email: 22 of them got one, against 134 of the 227 chats only Alex
answered (59%), and 54 of the 98 clients wrote nothing after the agent's first message. The agents came in a median
49 s after the client's first message; Alex had the contact a median 25 s in, and 116 s in at the 90th percentile.

Every chat enters the shared line at the client's first message (api/chat.py, first-message routing) and an agent
takes it blind, so the hold sits on the take, not on the line: the chat is seen waiting, and the Take button counts
down. Nothing else changes: a manager may still give the chat to someone, the chat still counts as waiting, and a
client who asks for a person (status needs_agent: in words, "I'll wait here", or a callback) is never held.

``CONTACT_HOLD_SECONDS`` (app_settings.flags, 120 by default): how long after the client's first message the take
waits. 0 turns the hold off within 30 s, without a deploy.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

HOLD_FLAG = "CONTACT_HOLD_SECONDS"
DEFAULT_HOLD_S = 120
# A typo in the flag (12000 for 120) must not lock every chat away from the agents for hours.
MAX_HOLD_S = 600

# /health: `refused` are takes the hold turned away (the rule working, not a failure); `unreadable_time` are chats
# whose first-message time could not be read, taken at once as before the hold (so a broken row never locks a
# client away from the agents) and counted, so "no hold" and "could not tell" read apart (CLAUDE.md rule 7).
HOLD_HEALTH: dict[str, Any] = {"refused": 0, "unreadable_time": 0, "last_refused_at": None}


def hold_seconds() -> int:
    """The flag, clamped to 0..MAX_HOLD_S. A value that is not a number keeps the default (app.flags logs it)."""
    from app.flags import flag

    try:
        value = int(flag(HOLD_FLAG, DEFAULT_HOLD_S))
    except (TypeError, ValueError) as e:
        logger.warning(f"[contact-hold] {HOLD_FLAG} unreadable ({type(e).__name__}): {DEFAULT_HOLD_S} s")
        value = DEFAULT_HOLD_S
    return max(0, min(MAX_HOLD_S, value))


def _when(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        out = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:  # noqa: silent — the caller counts an unreadable time (HOLD_HEALTH) and holds nothing
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def _engaged(conv: dict) -> bool:
    meta = conv.get("metadata") if isinstance(conv.get("metadata"), dict) else {}
    return bool(conv.get("engaged_agent_id") or meta.get("engaged_agent_id"))


def applies(conv: dict, *, has_contact: bool) -> bool:
    """The chat is one the hold is for: a sales chat Alex answers alone, open, unowned, with no contact yet, that no
    agent ever wrote in, and whose client did not ask for a person."""
    if not isinstance(conv, dict) or has_contact:
        return False
    if (conv.get("tunnel") or "sales") != "sales":
        return False
    if conv.get("status") != "active":
        return False
    if (conv.get("mode") or "ai") != "ai":
        return False
    if conv.get("assigned_agent_id") or _engaged(conv):
        return False
    return True


def hold_until(conv: dict, *, has_contact: bool, now: Optional[datetime] = None) -> Optional[datetime]:
    """When an agent may take this chat; None when they may take it now.

    The hold counts from the client's first message: the moment the chat entered the line (`queued_at`), or its
    creation when the line's time is missing (a migration not applied)."""
    seconds = hold_seconds()
    if seconds <= 0 or not applies(conv, has_contact=has_contact):
        return None
    raw = conv.get("queued_at") or conv.get("created_at")
    start = _when(raw)
    if start is None:
        if raw:
            HOLD_HEALTH["unreadable_time"] += 1
            logger.warning(f"[contact-hold] {conv.get('id')}: first-message time unreadable, no hold")
        return None
    until = start + timedelta(seconds=seconds)
    return until if until > (now or datetime.now(timezone.utc)) else None


def until_iso(conv: dict, *, has_contact: bool, now: Optional[datetime] = None) -> Optional[str]:
    """hold_until as the panel reads it (ISO 8601, UTC), or None."""
    until = hold_until(conv, has_contact=has_contact, now=now)
    return until.isoformat() if until else None


def seconds_left(until: datetime, now: Optional[datetime] = None) -> int:
    """Whole seconds until the take is allowed, at least 1 while the hold runs."""
    return max(1, math.ceil((until - (now or datetime.now(timezone.utc))).total_seconds()))


def count_refused() -> None:
    HOLD_HEALTH["refused"] += 1
    HOLD_HEALTH["last_refused_at"] = datetime.now(timezone.utc).isoformat()


def wait_after_hold(age_seconds: Optional[float], conv: dict, *, has_contact: bool) -> Optional[int]:
    """The client's wait an agent is measured on (agent_activity_log.response_seconds), without the hold.

    A chat taken with still no contact and no ask for a person was held the whole hold: the agent could take it only
    from then. Otherwise the hold ended earlier, at a moment the row does not keep (the contact or the ask), and the
    wait stays the line's, as before the hold."""
    if age_seconds is None:
        return None
    age = max(0, int(age_seconds))
    if applies(conv, has_contact=has_contact):
        return max(0, age - hold_seconds())
    return age
