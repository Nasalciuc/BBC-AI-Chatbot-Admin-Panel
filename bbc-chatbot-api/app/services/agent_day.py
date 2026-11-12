"""Your day — an operator's own numbers, built only from what the server keeps.

Nothing here is new tracking:
  * took / went back / closed come from agent_activity_log
    (claim_won, deadline_fired, closed);
  * the client's wait before the take is claim_won.response_seconds;
  * the first reply is measured from the messages table, from the take to the
    operator's first message. The `first_response` action exists but is logged
    too rarely to count on (5 rows in 60 days, Nov 2026);
  * "in the CRM" is leads.created_in_crm, whoever sent it.

Only the caller's own chats. There is no ranking and nobody else's numbers.

Days are cut at the operator's local midnight, from a fixed UTC offset the
panel sends. The production image ships without tzdata (see closing.py), so a
named zone is not an option; across a DST change a 30-day view can be an hour
off for the days before it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable, Optional

from app.services.client_presence import client_left

ALLOWED_DAYS = (1, 7, 30)


def parse_ts(value: Any) -> Optional[datetime]:
    """ISO string → aware datetime, or None. Never raises: a row we cannot
    read is left out of a number, not allowed to break the page."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:  # noqa: silent — an unreadable timestamp leaves that one row out of a number on the operator's own stats page; nothing is lost, sent or written
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def photo_of(user: Optional[dict]) -> Optional[str]:
    """A person's photo link (users.avatar_url), or None when they have none."""
    return ((user or {}).get("avatar_url") or "").strip() or None


def local_day_start(now: datetime, offset_minutes: int) -> datetime:
    """UTC instant of the operator's local midnight for `now`."""
    shift = timedelta(minutes=offset_minutes)
    local = now + shift
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - shift


def window_start(now: datetime, offset_minutes: int, days: int) -> datetime:
    """Oldest instant the page needs: the chosen range, at least the two
    weeks the week-over-week first reply compares."""
    span = max(14, days)
    return local_day_start(now, offset_minutes) - timedelta(days=span - 1)


def _first_name(row: dict) -> str:
    name = (row.get("visitor_name") or "").strip()
    return name.split()[0] if name else ""


def _local_date(ts: datetime, offset_minutes: int) -> str:
    return (ts + timedelta(minutes=offset_minutes)).date().isoformat()


def _med(values: Iterable[float]) -> Optional[int]:
    vals = [v for v in values if v is not None]
    return int(round(median(vals))) if vals else None


_TAKE_ENDS = ("claim_won", "deadline_fired", "closed")


def _first_reply_seconds(
    claims: list[dict], agent_messages: list[dict], events: list[dict]
) -> dict[int, Optional[int]]:
    """For each claim (by index): seconds from the take to the first operator
    message inside that take, or None when none came.

    A take lasts until the next take of the chat, the deadline sending it back
    to the assistant, or the chat being closed (the same rule as the owner
    Dashboard). Without that end, a chat that went back and was taken again
    would hand the second take's reply to the first one."""
    by_conv: dict[str, list[datetime]] = {}
    for m in agent_messages:
        t = parse_ts(m.get("created_at"))
        cid = m.get("conversation_id")
        if t and cid:
            by_conv.setdefault(cid, []).append(t)
    for times in by_conv.values():
        times.sort()
    ends: dict[str, list[datetime]] = {}
    for e in events:
        if e.get("action") in _TAKE_ENDS:
            ends.setdefault(e["conversation_id"], []).append(e["_at"])
    out: dict[int, Optional[int]] = {}
    for i, c in enumerate(claims):
        t0 = c["_at"]
        cid = c["conversation_id"]
        end = next((t for t in ends.get(cid, []) if t > t0), None)
        first = next((t for t in by_conv.get(cid, []) if t >= t0 and (end is None or t <= end)), None)
        out[i] = int((first - t0).total_seconds()) if first else None
    return out


def build_agent_day(
    *,
    now: datetime,
    offset_minutes: int,
    days: int,
    activity: list[dict],
    agent_messages: list[dict],
    conversations: dict[str, dict],
    leads: dict[str, dict],
    open_conversations: list[dict],
) -> dict:
    """Shape the page. Pure: every input is already fetched."""
    today_start = local_day_start(now, offset_minutes)
    range_start = today_start - timedelta(days=days - 1)
    table_days = max(7, days)

    events = []
    for a in activity:
        at = parse_ts(a.get("happened_at"))
        if at and a.get("conversation_id"):
            events.append({**a, "_at": at})
    events.sort(key=lambda e: e["_at"])

    claims = [e for e in events if e.get("action") == "claim_won"]
    replies = _first_reply_seconds(claims, agent_messages, events)
    for i, c in enumerate(claims):
        c["_reply_s"] = replies.get(i)

    def in_range(e: dict, start: datetime, end: Optional[datetime] = None) -> bool:
        return e["_at"] >= start and (end is None or e["_at"] < end)

    def crm_counts(claim_rows: list[dict]) -> tuple[int, int]:
        # The chat was really yours only if you answered in it. A chat that
        # went back to the assistant before your first message isn't counted.
        mine = {c["conversation_id"] for c in claim_rows if c.get("_reply_s") is not None}
        with_lead = [cid for cid in mine if cid in leads]
        in_crm = [cid for cid in with_lead if leads[cid].get("created_in_crm")]
        return len(with_lead), len(in_crm)

    def bucket(start: datetime, end: Optional[datetime]) -> dict:
        cl = [c for c in claims if in_range(c, start, end)]
        wl, ic = crm_counts(cl)
        return {
            "took": len(cl),
            "wait_median_s": _med(c.get("response_seconds") for c in cl),
            "first_reply_median_s": _med(c["_reply_s"] for c in cl),
            "went_back": sum(1 for e in events if e.get("action") == "deadline_fired" and in_range(e, start, end)),
            "closed": sum(1 for e in events if e.get("action") == "closed" and in_range(e, start, end)),
            "with_lead": wl,
            "in_crm": ic,
        }

    totals = bucket(range_start, None)
    totals["still_open"] = len(open_conversations)

    by_day = []
    for n in range(table_days):
        start = today_start - timedelta(days=n)
        end = start + timedelta(days=1)
        row = bucket(start, end)
        row["date"] = _local_date(start + timedelta(hours=12), offset_minutes)
        by_day.append(row)

    week_start = today_start - timedelta(days=6)
    prev_start = week_start - timedelta(days=7)
    this_week = _med(c["_reply_s"] for c in claims if in_range(c, week_start))
    prev_week = _med(c["_reply_s"] for c in claims if in_range(c, prev_start, week_start))

    went_back = []
    for e in events:
        if e.get("action") == "deadline_fired" and in_range(e, range_start):
            conv = conversations.get(e["conversation_id"], {})
            went_back.append({
                "conversation_id": e["conversation_id"],
                "first_name": _first_name(conv),
                "at": e["_at"].isoformat(),
            })
    went_back.reverse()  # newest first

    range_claims = [c for c in claims if in_range(c, range_start)]
    answered = []
    for c in reversed(range_claims):
        cid = c["conversation_id"]
        if c.get("_reply_s") is not None and cid not in answered:
            answered.append(cid)
    not_in_crm = []
    for cid in answered:
        lead = leads.get(cid)
        if not lead or lead.get("created_in_crm"):
            continue
        conv = conversations.get(cid, {})
        not_in_crm.append({
            "conversation_id": cid,
            "first_name": _first_name(conv),
            "has_phone": bool((conv.get("visitor_phone") or "").strip()),
            "has_email": bool((conv.get("visitor_email") or "").strip()),
            "status": conv.get("status"),
        })

    waiting_on_you = []
    for conv in open_conversations:
        last_user = parse_ts(conv.get("last_user_message_at"))
        last_agent = parse_ts(conv.get("last_agent_message_at"))
        if not last_user or (last_agent and last_agent >= last_user):
            continue
        gone = client_left(conv.get("metadata") or {}, conv.get("last_user_message_at"), now)
        waiting_on_you.append({
            "conversation_id": conv.get("id"),
            "first_name": _first_name(conv),
            "minutes": max(0, int((now - last_user).total_seconds() // 60)),
            "left_minutes_ago": gone // 60 if gone is not None else None,
        })
    waiting_on_you.sort(key=lambda w: -w["minutes"])

    return {
        "days": days,
        "tz_offset_minutes": offset_minutes,
        "now": now.isoformat(),
        "range_start": range_start.isoformat(),
        "totals": totals,
        "by_day": by_day,
        "went_back": went_back,
        "not_in_crm": not_in_crm,
        "waiting_on_you": waiting_on_you,
        "first_reply_week": {"this": this_week, "previous": prev_week},
    }
