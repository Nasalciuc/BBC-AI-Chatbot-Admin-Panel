"""Offers to send (ADR-6b, ADR-8b — 18 Nov 2026): the leads a consultant still owes a written offer.

Alex never states a fare (ADR-0): the consultant's written offer is the only way a client hears one. So
every lead with a contact and a trip waits here, oldest first, with the channel the client asked for,
until someone marks the offer sent, or not needed. Night chats are why: most Kayak chats come between
18:00 and 06:00 ET with no consultant online, and the follow-up tasks they left were never ticked.

One contact is enough (ADR-8b): an e-mail-only lead, which the CRM refuses until it takes a single
contact (ADR-8a), waits here like any other.

One client, one row: chats that share an e-mail or a phone are one person asking again; the row shows
the newest trip and waits since the oldest chat, and its mark goes on all of them.

Pure. The rows come from supabase.get_leads_universe(); the marks live on each conversation's metadata
(offer_sent_at / offer_skipped_at, merged server-side by patch_conv_presence — no migration).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from app.security.pii import mask_email, mask_name, mask_phone
from app.services.leads_view import crm_state

WINDOWS = (1, 3, 7, 14)
DEFAULT_DAYS = 3
DONE_LIMIT = 50
STATES = ("sent", "not_needed", "open")
# The order the panel names the wishes in; "call" is intent_signals.callback_requested.
CHANNELS = ("whatsapp", "text", "call", "email")
REFUSALS = ("no calls", "no texts", "no email")
# A lead the CRM's own check took for a test never waits for an offer.
_NOT_A_CLIENT = {"test_email"}


def _s(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _at(value) -> Optional[datetime]:
    raw = _s(value)
    if not raw:
        return None
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:  # noqa: silent — an unreadable time leaves the row out of a read-only list; nothing is written
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _digits(value) -> str:
    return re.sub(r"\D", "", _s(value))


def channels(row: dict) -> list[str]:
    """What the client asked to be reached by, in the panel's order. Empty: they named none, and any
    channel their contact allows will do."""
    parts = {p.strip() for p in _s(row.get("contact_channel")).split(",")}
    asked = set(parts)
    if str(row.get("callback_requested")).strip().lower() == "true":
        asked.add("call")
    return [c for c in CHANNELS if c in asked]


def refusals(row: dict) -> list[str]:
    """The channels the client turned down ("no calls", "no texts", "no email")."""
    parts = {p.strip() for p in _s(row.get("contact_channel")).split(",")}
    return [r for r in REFUSALS if r in parts]


def has_contact(row: dict) -> bool:
    return bool(_s(row.get("visitor_email")) or _s(row.get("visitor_phone")) or _s(row.get("phone_to_check")))


def has_trip(row: dict) -> bool:
    return bool(_s(row.get("origin_code")) and _s(row.get("destination_code")))


def mark_of(row: dict) -> Optional[tuple[str, datetime, Optional[str]]]:
    """("sent" | "not_needed", when, by whom) once someone marked the chat; None while it waits."""
    sent = _at(row.get("offer_sent_at"))
    if sent:
        return "sent", sent, row.get("offer_sent_by")
    skipped = _at(row.get("offer_skipped_at"))
    if skipped:
        return "not_needed", skipped, row.get("offer_skipped_by")
    return None


def _keys(row: dict) -> list[str]:
    """What makes two chats one client: the same e-mail, or the same number. A placed number (E.164) by
    all its digits: +1 210 555 0100 and +1 310 555 0100 are two clients, though they end alike
    (CodeRabbit #270). A number to check has no country, so nothing says which placed number it is: it
    matches only the same number to check."""
    keys = [f"c:{row.get('conversation_id')}"]
    email = _s(row.get("visitor_email")).lower()
    if email:
        keys.append(f"e:{email}")
    placed = _digits(row.get("visitor_phone"))
    if len(placed) >= 7:
        keys.append(f"p:{placed}")
    local = _digits(row.get("phone_to_check"))
    if len(local) >= 7:
        keys.append(f"t:{local}")
    return keys


def group_by_client(rows: list[dict]) -> list[list[dict]]:
    """Chats that share an e-mail or a phone, together (a client who came back is one client)."""
    parent: dict[str, str] = {}

    def find(k: str) -> str:
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for row in rows:
        keys = _keys(row)
        for k in keys[1:]:
            a, b = find(keys[0]), find(k)
            if a != b:
                parent[a] = b
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(find(_keys(row)[0]), []).append(row)
    return list(groups.values())


def _newest_first(group: list[dict]) -> list[dict]:
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(group, key=lambda r: _at(r.get("created_at")) or epoch, reverse=True)


def _first(group: list[dict], key: str) -> Optional[str]:
    for row in group:
        if _s(row.get(key)):
            return _s(row.get(key))
    return None


def _route(row: dict) -> str:
    return _s(row.get("route_display")) or f"{_s(row.get('origin_code'))} → {_s(row.get('destination_code'))}"


def _chats(group: list[dict]) -> list[dict]:
    return [{"conversation_id": r.get("conversation_id"), "chat_number": r.get("chat_number")} for r in group]


def owner_of(row: dict) -> Optional[str]:
    """The agent a lead belongs to: given to them, closed by them, or the one who took the chat."""
    return row.get("assigned_agent_id") or row.get("closed_by_agent_id") or row.get("engaged_agent_id") or None


def followup_line(row: dict, names: dict[str, str]) -> Optional[dict]:
    """How the lead reached its agent, for "Given by the lottery to Charlie at 09:12" (lead_lottery)."""
    f = row.get("followup")
    if not isinstance(f, dict) or not f.get("by"):
        return None
    agent = f.get("agent_id")
    return {
        "by": f.get("by"),
        "state": f.get("state"),
        "at": f.get("assigned_at"),
        "agent_name": names.get(agent) if agent else None,
    }


def _client_row(group: list[dict], *, masked: bool, blind: bool, hidden: bool = False) -> dict:
    """The fields a row shares, waiting or done: who, how to reach them, the newest trip.

    hidden: another agent's lead (with the follow-up lottery on): the route and whose it is, never how
    to reach the client."""
    main = group[0]
    email, phone = _first(group, "visitor_email"), _first(group, "visitor_phone")
    to_check = None if phone else _first(group, "phone_to_check")
    name = _first(group, "visitor_name")
    if masked or hidden:
        name, email, phone, to_check = mask_name(name), mask_email(email), mask_phone(phone), mask_phone(to_check)
    if hidden:
        email = phone = to_check = None
    out = {
        "conversation_id": main.get("conversation_id"),
        "lead_id": main.get("id"),
        "chat_number": main.get("chat_number"),
        "client": name,
        "email": email,
        "phone": phone,
        "phone_to_check": to_check,
        "route": _route(main),
        "departure_date": main.get("departure_date"),
        "return_date": main.get("return_date"),
        "trip_type": main.get("trip_type"),
        "passengers": main.get("passengers"),
        "children_count": main.get("children_count"),
        "infant_count": main.get("infant_count"),
        "cabin_class": main.get("cabin_class"),
        "chats": _chats(group),
        "blind": blind,
        "hidden": hidden and not blind,
    }
    if blind:
        # Blind take (the chats' rule): an agent learns who waits and since when, never the trip or how to
        # reach them, until the chat is theirs.
        for key in ("email", "phone", "phone_to_check", "route", "departure_date", "return_date", "trip_type",
                    "passengers", "children_count", "infant_count", "cabin_class"):
            out[key] = None
    return out


def candidates(rows: list[dict], *, now: datetime, days: int = DEFAULT_DAYS) -> list[dict]:
    """The rows the queue is made of, waiting or marked: not support's, with a contact and a route, made in
    the last `days` days, and not taken for a test by the CRM's check. The ones whose blind state the
    endpoint reads fresh for an agent (app/api/offers.py)."""
    since = now - timedelta(days=days if days in WINDOWS else DEFAULT_DAYS)
    out = []
    for row in rows:
        if (row.get("tunnel") or "sales") == "support" or not has_contact(row) or not has_trip(row):
            continue
        if _s(row.get("crm_push_gate_reason")) in _NOT_A_CLIENT:
            continue
        made = _at(row.get("created_at"))
        if not made or made < since:
            continue
        out.append(row)
    return out


def build_offers(
    rows: list[dict],
    *,
    now: datetime,
    days: int = DEFAULT_DAYS,
    names: Optional[dict[str, str]] = None,
    masked: bool = False,
    blind: Optional[Callable[[dict], bool]] = None,
    hidden: Optional[Callable[[dict], bool]] = None,
    owner: Optional[Callable[[dict], Optional[str]]] = None,
) -> dict:
    """The queue: {"waiting": oldest first, "done": newest first, "counts"}.

    A lead waits when its chat is not support's, it has a contact and a route, it was made in the last
    `days` days, the CRM's check did not take it for a test, and nobody marked it. `owner`: whose a row is,
    read as the caller's visibility rules read it (the offers API reads it fresh); owner_of the row itself
    otherwise."""
    names = names or {}
    blind = blind or (lambda _row: False)
    hidden = hidden or (lambda _row: False)
    owner = owner or owner_of
    waiting_rows: list[dict] = []
    done_rows: list[dict] = []
    for row in candidates(rows, now=now, days=days):
        (done_rows if mark_of(row) else waiting_rows).append(row)

    waiting = []
    for group in group_by_client(waiting_rows):
        group = _newest_first(group)
        main = group[0]
        oldest = min(_at(r.get("created_at")) for r in group)
        wished = next((r for r in group if channels(r) or refusals(r)), main)
        state = crm_state(main)
        agent = owner(main)  # given, closed by, or the one who took the chat: the rule that hides it
        is_hidden = any(hidden(r) for r in group)
        row = _client_row(group, masked=masked, blind=any(blind(r) for r in group), hidden=is_hidden)
        row.update({
            "channels": channels(wished),
            "refused": refusals(wished),
            "price_seen": _first(group, "price_seen"),
            "crm_state": state,
            "crm_gate": (_s(main.get("crm_push_gate_reason")) or None) if state == "refused" else None,
            "crm_lead_id": main.get("crm_lead_id"),
            "agent_name": names.get(agent) if agent else None,
            "agent_id": agent,
            "followup": followup_line(main, names),
            "chat_status": main.get("conversation_status"),
            "waiting_since": oldest.astimezone(timezone.utc).isoformat(),
        })
        if is_hidden:
            row["crm_lead_id"] = None  # a handle on the client in the CRM: theirs, not this agent's
        if row["blind"]:
            # The wishes, the price and the CRM's verdict come from the client's words and contact: they wait
            # for the take too.
            row.update({"channels": [], "refused": [], "price_seen": None, "crm_state": None, "crm_gate": None,
                        "crm_lead_id": None})
        waiting.append(row)
    waiting.sort(key=lambda r: r["waiting_since"])

    done = []
    for group in group_by_client(done_rows):
        group = _newest_first(group)
        state, at, by = max((mark_of(r) for r in group), key=lambda m: m[1])
        row = _client_row(group, masked=masked, blind=any(blind(r) for r in group),
                          hidden=any(hidden(r) for r in group))
        row.update({"state": state, "at": at.astimezone(timezone.utc).isoformat(), "by_name": names.get(by) if by else None})
        done.append(row)
    done.sort(key=lambda r: r["at"], reverse=True)

    return {
        "waiting": waiting,
        "done": done[:DONE_LIMIT],
        "counts": {
            "waiting": len(waiting),
            "sent": sum(1 for d in done if d["state"] == "sent"),
            "not_needed": sum(1 for d in done if d["state"] == "not_needed"),
        },
    }


# The metadata keys a mark writes (and the ones a failed mark puts back as they were: app/api/offers.py).
MARK_KEYS: tuple[str, ...] = ("offer_sent_at", "offer_sent_by", "offer_skipped_at", "offer_skipped_by")


def mark_patch(state: str, user_id: str, now: datetime) -> dict:
    """The metadata keys a mark writes: the one it sets, the other cleared; "open" clears both."""
    at = now.isoformat()
    cleared = dict.fromkeys(MARK_KEYS)
    if state == "sent":
        return {**cleared, "offer_sent_at": at, "offer_sent_by": user_id}
    if state == "not_needed":
        return {**cleared, "offer_skipped_at": at, "offer_skipped_by": user_id}
    return cleared
