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

from app.security.pii import mask_email, mask_name, mask_phone

VIEWS = ("not_in_crm", "to_review", "all")
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
    return True


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def matches(row: dict, search: str, *, masked: bool = False) -> bool:
    """Name, e-mail, route, chat number or 4+ phone digits.

    `masked`: the caller sees contact details masked (a supervisor). The
    search then reads only what they see, so the match and the count can't
    answer a question about a hidden name, e-mail or phone number."""
    q = search.strip().lower()
    if not q:
        return True
    name, email, phone = row.get("visitor_name"), row.get("visitor_email"), row.get("visitor_phone")
    if masked:
        name, email, phone = mask_name(name), mask_email(email), mask_phone(phone)
    hay = " ".join(
        str(v or "") for v in
        (name, email, row.get("origin_code"), row.get("destination_code"), row.get("route_display"))
    ).lower()
    if q in hay:
        return True
    digits = _digits(q)
    if digits and q.lstrip("#").replace(" ", "").isdigit():
        if str(row.get("chat_number") or "") == digits:
            return True
        return len(digits) >= 4 and digits in _digits(str(phone or ""))
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
    masked: bool = False,
) -> dict:
    """`masked`: the reader sees contact details masked; the search follows (matches)."""
    decorated = [decorate(r, names) for r in rows]
    counts = {v: sum(1 for r in decorated if in_view(r, v)) for v in VIEWS}
    tab = [r for r in decorated if in_view(r, view)]
    filtered = [
        r for r in tab
        if (not level or r["level"] == level)
        and (not with_ or r["chat_with"] == with_)
        and matches(r, search or "", masked=masked)
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
    return out
