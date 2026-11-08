"""Leads: what reached the CRM and what didn't — one shape for the list, its
tabs, its filters and its counts, so the numbers on the page always agree.

Pure. The rows come from supabase.get_leads_universe(): every lead with the
fields of its conversation the page needs (confirmed_at, the chat's status,
who closed it, whether an agent wrote).

The level is what the chat collected and what the client confirmed, not how
likely they are to buy:
  ready   — the client said YES to the assistant's summary (confirmed_at);
  almost  — route and a departure date, no YES;
  early   — a route only;
  contact — the form, no trip.
The panel's levelOf (features/chats/view.ts) follows the same rule.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, Optional

VIEWS = ("not_in_crm", "to_review", "reviewed", "all")
LEVELS = ("ready", "almost", "early", "contact")
CHAT_WITH = ("person", "ai")
ORDERS = ("newest", "oldest", "closest")
OPEN_STATUSES = ("active", "needs_agent", "waiting")
_STEPS = {"ready": 4, "almost": 3, "early": 2, "contact": 1}


def _has(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def lead_level(row: dict) -> str:
    if not row.get("origin_code") and not row.get("destination_code"):
        return "contact"
    if row.get("confirmed_at"):
        return "ready"
    if row.get("departure_date"):
        return "almost"
    return "early"


def chat_with(row: dict) -> str:
    """"person" when an operator wrote, holds the chat or closed it."""
    if row.get("last_agent_message_at") or row.get("assigned_agent_id") or row.get("closed_by_agent_id"):
        return "person"
    return "ai"


def crm_state(row: dict) -> str:
    """Where the lead stands with the CRM. Closing a chat doesn't send it."""
    if row.get("created_in_crm"):
        return "in_crm"
    if not _has(row.get("visitor_phone")):
        return "no_phone"
    if _has(row.get("crm_push_gate_reason")):
        return "refused"
    if row.get("conversation_status") in OPEN_STATUSES:
        return "open"
    if row.get("closed_by_agent_id"):
        return "closed_by_agent"
    if lead_level(row) == "ready":
        return "ready_never_sent"
    return "never_sent"


def decorate(row: dict, names: dict[str, str]) -> dict:
    """The list row: the lead, its conversation's fields, and the three
    derived values the page filters and counts on."""
    out = dict(row)
    out["level"] = lead_level(row)
    out["chat_with"] = chat_with(row)
    out["crm_state"] = crm_state(row)
    agent = row.get("assigned_agent_id") or row.get("closed_by_agent_id")
    out["agent_name"] = names.get(agent) if agent else None
    closer = row.get("closed_by_agent_id")
    out["closed_by_name"] = names.get(closer) if closer else None
    return out


def in_view(row: dict, view: str) -> bool:
    if view == "not_in_crm":
        return row["crm_state"] not in ("in_crm", "no_phone")
    if view == "to_review":
        return row["level"] == "ready" and not row.get("reviewed_by_qa")
    if view == "reviewed":
        return bool(row.get("reviewed_by_qa"))
    return True


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def matches(row: dict, search: str) -> bool:
    q = search.strip().lower()
    if not q:
        return True
    hay = " ".join(
        str(row.get(k) or "") for k in
        ("visitor_name", "visitor_email", "origin_code", "destination_code", "route_display")
    ).lower()
    if q in hay:
        return True
    digits = _digits(q)
    if digits and q.lstrip("#").replace(" ", "").isdigit():
        if str(row.get("chat_number") or "") == digits:
            return True
        phone = _digits(str(row.get("visitor_phone") or ""))
        return len(digits) >= 4 and digits in phone
    return False


def _ts(row: dict) -> float:
    """created_at as seconds; 0 when unreadable, so the row sorts last."""
    raw = row.get("created_at") or ""
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:  # noqa: silent — an unreadable created_at sorts the row last on a read-only list; nothing is written
        return 0.0


def order_rows(rows: list[dict], order: str) -> list[dict]:
    if order == "oldest":
        return sorted(rows, key=_ts)
    if order == "closest":
        return sorted(rows, key=lambda r: (-_STEPS[r["level"]], -(r.get("score") or 0), -_ts(r)))
    return sorted(rows, key=_ts, reverse=True)


def default_order(view: str) -> str:
    # Review the oldest first; everything else newest first.
    return "oldest" if view == "to_review" else "newest"


def _parse(raw: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if raw else None
    except ValueError:  # noqa: silent — an unreadable reviewed_at just isn't counted as today's on a read-only card
        return None


def review_summary(rows: Iterable[dict], *, viewer_id: Optional[str], day_start: datetime) -> dict:
    """The cards above QA's lists: what's left, what this person reviewed
    today (their own day), and how many left to review never had a person."""
    rows = list(rows)
    left = [r for r in rows if in_view(r, "to_review")]
    mine_today = 0
    for r in rows:
        at = _parse(r.get("reviewed_at"))
        if r.get("reviewed_by_qa") and viewer_id and r.get("reviewed_by") == viewer_id and at and at >= day_start:
            mine_today += 1
    return {
        "to_review": len(left),
        "reviewed_today_by_me": mine_today,
        "ai_only_to_review": sum(1 for r in left if r["chat_with"] == "ai"),
    }


def summary_of(rows: Iterable[dict]) -> dict:
    """The cards above "Not in the CRM": why each one isn't there."""
    out: dict[str, Any] = {"open": 0, "closed_by_agent": 0, "ready_never_sent": 0, "never_sent": 0, "refused": {}}
    for r in rows:
        state = r["crm_state"]
        if state == "refused":
            reason = (r.get("crm_push_gate_reason") or "").strip()
            out["refused"][reason] = out["refused"].get(reason, 0) + 1
        elif state in out:
            out[state] += 1
    return out


def build_leads_view(
    rows: list[dict],
    *,
    view: str,
    names: dict[str, str],
    search: Optional[str] = None,
    level: Optional[str] = None,
    with_: Optional[str] = None,
    order: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    viewer_id: Optional[str] = None,
    day_start: Optional[datetime] = None,
) -> dict:
    decorated = [decorate(r, names) for r in rows]
    counts = {v: sum(1 for r in decorated if in_view(r, v)) for v in VIEWS}
    tab = [r for r in decorated if in_view(r, view)]
    filtered = [
        r for r in tab
        if (not level or r["level"] == level)
        and (not with_ or r["chat_with"] == with_)
        and matches(r, search or "")
    ]
    ordered = order_rows(filtered, order or default_order(view))
    out = {
        "rows": ordered[offset:offset + limit],
        "count": len(ordered),
        "counts": counts,
        "order": order or default_order(view),
    }
    if view == "not_in_crm":
        out["summary"] = summary_of(tab)
    if view in ("to_review", "reviewed") and day_start is not None:
        out["review"] = review_summary(decorated, viewer_id=viewer_id, day_start=day_start)
    return out
