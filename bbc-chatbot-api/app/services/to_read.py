"""To read: the chats a supervisor (or the owner) should read this week.

Pure; the reads live in supabase.get_to_read_inputs / get_reading_inputs.

Lost — fixed rules, no AI, on takes this week (Monday 00:00 in the reader's
time zone, until now):
  back    — nobody answered in time and the chat went back to the assistant
            (the take ended with deadline_fired);
  anyone  — while a person had the chat, the client asked whether anyone was
            there ("Are you there?", "?", "I'm waiting");
  never   — a person had the chat and sent nothing, and it didn't go back to
            the assistant (closed, or given to someone else);
  yes     — the client had confirmed the trip to the assistant before the take,
            so the person came in on a finished trip (whether they asked again
            only reading tells).
A take is the dashboard's take (owner_dashboard.build_takes), so a chat lost
here is counted the same way on the Dashboard.

From your team — last week's chats (Monday to Sunday), picked on Monday: two
of each person's at random, and one more for anyone past the Dashboard's help
line. The pick is a hash of the week, the person and the chat, so it doesn't
change during the week and needs nothing stored. Lost chats are left out:
samples show everyday work. "Read" is kept in the reader's browser.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Optional

from app.services.agent_day import local_day_start, parse_ts
from app.services.owner_dashboard import (
    MIN_AGENT_CHATS,
    REPLY_LINE_S,
    WENT_BACK_SHARE,
    build_takes,
)

SAMPLE_PER_PERSON = 2
REASONS = ("back", "anyone", "never", "yes")  # most serious first
_REASON_RANK = {k: i for i, k in enumerate(REASONS)}

# What clients actually write when nobody answers (Oct–Nov 2026): "?", "??",
# "Are you there", "are you still there?", "Still there?", "I'm  waiting".
ANYONE_THERE = re.compile(
    r"^\s*\?+\s*$"
    r"|^\s*(?:hello|hi|hey)\s*\?+\s*$"
    r"|\bare\s+(?:you|u)\s+(?:still\s+)?(?:there|here)\b"
    r"|\bstill\s+there\b"
    r"|\b(?:any\s?(?:one|body)|some\s?one)\s+(?:there|here|available|around)\b"
    r"|\bi'?\s?a?m\s+(?:still\s+)?waiting\s*(?:[.!?]|$|for\s+(?:you|a\s+reply|an\s+answer|someone))"
    r"|\bhello\s*\?",
    re.I,
)


def is_anyone_there(text: Any) -> bool:
    return isinstance(text, str) and bool(ANYONE_THERE.search(text))


def week_start(now: datetime, offset_minutes: int) -> datetime:
    """Monday 00:00 in the reader's time zone, as UTC."""
    day = local_day_start(now, offset_minutes)
    local_weekday = (now + timedelta(minutes=offset_minutes)).weekday()
    return day - timedelta(days=local_weekday)


def client_label(name: Any, *, masked: bool) -> str:
    """"Olga V." — or, for a supervisor, the masked name Conversations shows."""
    from app.security.pii import mask_name

    text = name.strip() if isinstance(name, str) else ""
    if not text:
        return ""
    if masked:
        return mask_name(text) or ""
    parts = text.split()
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0]}."


def route_of(lead: Optional[dict]) -> Optional[str]:
    if not lead:
        return None
    if isinstance(lead.get("route_display"), str) and lead["route_display"].strip():
        return lead["route_display"].strip()
    a, b = lead.get("origin_code"), lead.get("destination_code")
    return f"{a} → {b}" if a and b else None


def _pick_rank(week: str, user_id: str, conv_id: str) -> str:
    return hashlib.sha256(f"{week}:{user_id}:{conv_id}".encode()).hexdigest()


def lost_reasons(conv: dict, takes: list[dict], client_msgs: list[dict]) -> list[dict]:
    """Every rule the chat broke, the earliest time of each, and whose take it was."""
    found: dict[str, dict] = {}

    def add(key: str, at: Optional[datetime], user_id: Optional[str]) -> None:
        if at and (key not in found or at < found[key]["_at"]):
            found[key] = {"key": key, "_at": at, "user_id": user_id}

    yes = parse_ts(conv.get("confirmed_at"))
    for t in takes:
        if t["ended_by"] == "deadline_fired":
            add("back", t["end_at"], t["user_id"])
        elif t["reply_s"] is None and not t["fresh"]:
            add("never", t["end_at"] or t["at"], t["user_id"])
        for m in client_msgs:
            at = m["_at"]
            if at >= t["at"] and (t["end_at"] is None or at <= t["end_at"]) and is_anyone_there(m.get("content")):
                add("anyone", at, t["user_id"])
                break
        if yes and yes < t["at"]:
            add("yes", t["at"], t["user_id"])
    rows = sorted(found.values(), key=lambda r: (_REASON_RANK[r["key"]], r["_at"]))
    return [{"key": r["key"], "at": r["_at"].isoformat(), "user_id": r["user_id"]} for r in rows]


def _needs_help(takes: list[dict]) -> bool:
    """The Dashboard's line: 3+ chats, a median first reply over 60 s, and a
    third of the chats back to the assistant."""
    chats = {t["conversation_id"] for t in takes}
    back = {t["conversation_id"] for t in takes if t["ended_by"] == "deadline_fired"}
    replies = [t["reply_s"] for t in takes if t["reply_s"] is not None]
    reply = median(replies) if replies else None
    return (
        len(chats) >= MIN_AGENT_CHATS and reply is not None and reply > REPLY_LINE_S
        and len(back) >= len(chats) * WENT_BACK_SHARE
    )


def build_to_read(
    *,
    now: datetime,
    offset_minutes: int,
    activity: list[dict],
    conversations: list[dict],
    leads: list[dict],
    messages: list[dict],
    users: dict[str, dict],
    team_ids: Optional[set[str]] = None,
    masked: bool = False,
) -> dict:
    """The To read list. `team_ids` set: only chats of those teams (a
    supervisor; none means nothing). `masked`: clients' names masked."""
    this_week = week_start(now, offset_minutes)
    last_week = this_week - timedelta(days=7)
    convs = {
        c["id"]: c for c in conversations
        if c.get("id") and (team_ids is None or c.get("team_id") in team_ids)
    }
    lead_of = {row["conversation_id"]: row for row in leads if row.get("conversation_id")}
    activity = [a for a in activity if a.get("conversation_id") in convs]
    agent_msgs = [m for m in messages if m.get("role") == "agent" and m.get("conversation_id") in convs]
    client_msgs: dict[str, list[dict]] = defaultdict(list)
    for m in messages:
        at = parse_ts(m.get("created_at"))
        if m.get("role") == "user" and at and m.get("conversation_id") in convs:
            client_msgs[m["conversation_id"]].append({**m, "_at": at})
    for v in client_msgs.values():
        v.sort(key=lambda m: m["_at"])

    takes = build_takes(activity, agent_msgs, now)
    by_conv: dict[str, list[dict]] = defaultdict(list)
    for t in takes:
        by_conv[t["conversation_id"]].append(t)

    def name_of(uid: Optional[str]) -> Optional[str]:
        return (users.get(uid) or {}).get("name") if uid else None

    def row(cid: str, kind: str, at: datetime, user_id: Optional[str], reasons: list[dict]) -> dict:
        c = convs[cid]
        return {
            "id": cid, "chat_number": c.get("chat_number"), "kind": kind, "at": at.isoformat(),
            "client": client_label(c.get("visitor_name"), masked=masked),
            "agent_id": user_id, "agent_name": name_of(user_id),
            "route": route_of(lead_of.get(cid)), "status": c.get("status"),
            "reasons": [{**r, "name": name_of(r["user_id"])} for r in reasons],
        }

    lost: list[dict] = []
    lost_ids: set[str] = set()
    for cid, ts in by_conv.items():
        reasons = lost_reasons(convs[cid], ts, client_msgs.get(cid, []))
        if not reasons:
            continue
        lost_ids.add(cid)  # left out of the sample whichever week it was lost in
        recent = [r for r in reasons if (parse_ts(r["at"]) or now) >= this_week]
        if recent:
            at = min(parse_ts(r["at"]) or now for r in recent)
            # The row belongs to whoever had the chat at its most serious rule.
            lost.append(row(cid, "lost", at, recent[0]["user_id"], recent))
    lost.sort(key=lambda r: r["at"], reverse=True)

    # The reader's week, in their own calendar: Monday 00:00 at +120 is Sunday
    # 22:00 UTC, so the UTC instant would name the ISO week before.
    week_key = (last_week + timedelta(minutes=offset_minutes)).strftime("%G-W%V")
    mine: dict[str, list[dict]] = defaultdict(list)
    for t in takes:
        if t["user_id"] and last_week <= t["at"] < this_week:
            mine[t["user_id"]].append(t)
    sample: list[dict] = []
    for uid, ts in mine.items():
        pool = {t["conversation_id"]: t for t in ts if t["reply_s"] is not None and t["conversation_id"] not in lost_ids}
        ranked = sorted(pool, key=lambda cid: _pick_rank(week_key, uid, cid))
        picks = [(cid, "sample") for cid in ranked[:SAMPLE_PER_PERSON]]
        if _needs_help(ts) and len(ranked) > SAMPLE_PER_PERSON:
            picks.append((ranked[SAMPLE_PER_PERSON], "help"))
        for cid, kind in picks:
            sample.append(row(cid, kind, pool[cid]["at"], uid, []))
    sample.sort(key=lambda r: (r["agent_name"] or "", r["at"]))

    people = sorted(
        {r["agent_id"] for r in lost + sample if r["agent_id"]},
        key=lambda uid: name_of(uid) or "",
    )
    return {
        "week_start": this_week.isoformat(),
        "sample_week": {"start": last_week.isoformat(), "end": this_week.isoformat()},
        "lost": lost,
        "sample": sample,
        "people": [{"id": uid, "name": name_of(uid)} for uid in people],
    }


# ── One chat, for reading ────────────────────────────────────────────────────

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w/])\+?\d[\d\s().-]{5,}\d(?![\w/])")
_DATE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$|^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$")


def mask_contacts(text: Any) -> Any:
    """E-mails and phone numbers inside a message, masked the way the rest of
    a supervisor's panel masks them. Dates and prices stay."""
    from app.security.pii import mask_email, mask_phone

    if not isinstance(text, str):
        return text

    def phone(m: re.Match) -> str:
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)
        if not 7 <= len(digits) <= 15 or _DATE.match(raw.strip()):
            return raw
        return mask_phone(raw) or raw

    return _PHONE.sub(phone, _EMAIL.sub(lambda m: mask_email(m.group(0)) or m.group(0), text))


def build_reading(
    *,
    now: datetime,
    offset_minutes: int,
    conv: dict,
    lead: Optional[dict],
    messages: list[dict],
    activity: list[dict],
    ready_log: list[dict],
    agent_week: list[dict],
    users: dict[str, dict],
    masked: bool,
) -> dict:
    """One chat as the reader shows it: the messages (each operator message
    with its sender), what happened, the rules it broke, the trip and lead,
    and how the person's week went. `agent_week` is that person's activity
    since Monday."""
    from app.security.pii import mask_email, mask_phone
    from app.services.leads_view import crm_state, lead_level

    def name_of(uid: Optional[str]) -> Optional[str]:
        return (users.get(uid) or {}).get("name") if uid else None

    msgs = []
    for m in messages:
        at = parse_ts(m.get("created_at"))
        if at and m.get("role") in ("user", "ai", "agent", "system"):
            msgs.append({**m, "_at": at})
    msgs.sort(key=lambda m: m["_at"])
    agent_msgs = [{"conversation_id": conv["id"], "created_at": m["created_at"]} for m in msgs if m["role"] == "agent"]
    takes = [t for t in build_takes(activity, agent_msgs, now) if t["conversation_id"] == conv["id"]]

    def sender(at: datetime) -> Optional[str]:
        owner = None
        for t in takes:
            if t["at"] <= at:
                owner = t["user_id"]
        return owner

    out_msgs = []
    for m in msgs:
        who = sender(m["_at"]) if m["role"] == "agent" else None
        out_msgs.append({
            "id": m.get("id"), "role": m["role"], "at": m["_at"].isoformat(),
            "text": mask_contacts(m.get("content")) if masked else m.get("content"),
            "user_id": who, "name": name_of(who),
        })

    events: list[dict] = [{"at": conv.get("created_at"), "kind": "started"}]
    for t in takes:
        events.append({"at": t["at"].isoformat(), "kind": "taken", "user_id": t["user_id"], "name": name_of(t["user_id"])})
        if t["reply_s"] is not None:
            events.append({
                "at": (t["at"] + timedelta(seconds=t["reply_s"])).isoformat(), "kind": "first_reply",
                "user_id": t["user_id"], "name": name_of(t["user_id"]), "seconds": t["reply_s"],
            })
        if t["ended_by"] in ("deadline_fired", "closed") and t["end_at"]:
            events.append({
                "at": t["end_at"].isoformat(), "kind": "went_back" if t["ended_by"] == "deadline_fired" else "closed",
                "user_id": t["user_id"], "name": name_of(t["user_id"]),
            })
    involved = {t["user_id"] for t in takes if t["user_id"]}
    start = parse_ts(conv.get("created_at"))
    end = parse_ts(conv.get("closed_at")) or now
    for r in ready_log:
        at = parse_ts(r.get("changed_at"))
        if r.get("user_id") in involved and at and start and start <= at <= end and r.get("is_ready") is False:
            events.append({"at": at.isoformat(), "kind": "not_ready", "user_id": r["user_id"], "name": name_of(r["user_id"])})
    if conv.get("status") == "closed" and conv.get("closed_at") and not any(e["kind"] == "closed" for e in events):
        events.append({"at": conv["closed_at"], "kind": "ended"})
    events.sort(key=lambda e: parse_ts(e["at"]) or now)

    client_msgs = [m for m in msgs if m["role"] == "user"]
    reasons = lost_reasons(conv, takes, client_msgs)
    reasons = [{**r, "name": name_of(r["user_id"])} for r in reasons]
    last_person = takes[-1]["user_id"] if takes else None

    lead_out = None
    if lead:
        joined = {**lead, "visitor_phone": conv.get("visitor_phone"), "confirmed_at": conv.get("confirmed_at"),
                  "conversation_status": conv.get("status"), "closed_by_agent_id": conv.get("closed_by_agent_id")}
        phone, email = conv.get("visitor_phone"), conv.get("visitor_email")
        lead_out = {
            "id": lead.get("id"), "route": route_of(lead), "origin_code": lead.get("origin_code"),
            "destination_code": lead.get("destination_code"), "departure_date": lead.get("departure_date"),
            "return_date": lead.get("return_date"), "passengers": lead.get("passengers"),
            "trip_type": lead.get("trip_type"), "cabin_class": lead.get("cabin_class"),
            "level": lead_level(joined), "crm_state": crm_state(joined), "crm_lead_id": lead.get("crm_lead_id"),
            "crm_push_gate_reason": lead.get("crm_push_gate_reason"),
            "phone": mask_phone(phone) if masked else phone,
            "email": mask_email(email) if masked else email,
        }

    week_takes = build_takes(agent_week, [], now) if last_person else []
    mine = [t for t in week_takes if t["user_id"] == last_person]
    week = None
    if last_person:
        week = {
            "user_id": last_person, "name": name_of(last_person),
            "chats": len({t["conversation_id"] for t in mine}),
            "went_back": len({t["conversation_id"] for t in mine if t["ended_by"] == "deadline_fired"}),
            "since": week_start(now, offset_minutes).isoformat(),
        }

    return {
        "id": conv["id"], "chat_number": conv.get("chat_number"),
        "client": client_label(conv.get("visitor_name"), masked=masked),
        "status": conv.get("status"), "tunnel": conv.get("tunnel"),
        "created_at": conv.get("created_at"), "closed_at": conv.get("closed_at"),
        "person": {"user_id": last_person, "name": name_of(last_person)} if last_person else None,
        "messages": out_msgs, "events": events, "reasons": reasons,
        "lead": lead_out, "week": week,
    }
