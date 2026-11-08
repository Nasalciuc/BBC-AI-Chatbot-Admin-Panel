"""Team · Now — the floor as it is right now, and one person's day.

Pure; the reads live in supabase.get_team_now_inputs / get_person_day_inputs.
Nothing here is new tracking:
  * Ready and "sends no signal" come from users.is_ready and last_seen_at
    against the server's own windows: out of the shared queue after
    queue_presence_window_seconds (90 s) without a heartbeat, Ready switched
    off after agent_timeout_seconds (600 s);
  * the queue is the shared line's own rule (queued_at set, nobody assigned,
    status active or needs_agent);
  * a client waiting for a person is last_user_message_at newer than
    last_agent_message_at;
  * takes, first replies and went-backs are the Dashboard's (build_takes),
    cut at the viewer's local midnight;
  * hours online are agent_presence_daily.minutes_online.

"On shift" isn't used: shift times have no time zone and almost nobody is in
a team (Nov 2026), so the board counts people online instead.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median
from typing import Optional

from app.services.agent_day import local_day_start, parse_ts
from app.services.owner_dashboard import build_takes
from app.services.to_read import client_label, route_of

OPERATOR_ROLES = ("sales", "support")
CLIENT_WAIT_LINE_S = 300   # a client waiting this long for a reply in a person's chat
QUEUE_WAIT_LINE_S = 180    # the oldest chat in the shared queue
WENT_BACK_SHOWN = 3        # people with a chat back to the assistant today, newest first


def _med(values) -> Optional[int]:
    vals = [v for v in values if v is not None]
    return int(round(median(vals))) if vals else None


def presence_of(user: dict, now: datetime, *, queue_window_s: int, online_s: int) -> str:
    """ready — Ready and in the queue; no_signal — Ready, but the panel has
    been silent past the queue window, so out of the queue until it comes
    back (Ready switches off at online_s); online — not Ready; offline."""
    seen = parse_ts(user.get("last_seen_at"))
    age = (now - seen).total_seconds() if seen else None
    if user.get("is_ready"):
        if age is not None and age <= queue_window_s:
            return "ready"
        return "no_signal"
    if age is not None and age <= online_s:
        return "online"
    return "offline"


def ready_since(user_id: str, ready_log: list[dict]) -> Optional[str]:
    """When the person last switched Ready on (None if not in the log read)."""
    last = None
    for r in ready_log:
        if r.get("user_id") == user_id:
            last = r
    if last and last.get("is_ready"):
        return last.get("changed_at")
    return None


def ready_spans(user_id: str, ready_log: list[dict], day_start: datetime, *, fallback: bool) -> list[list[Optional[str]]]:
    """Ready intervals since `day_start` ([start, end], end None = still Ready).
    The state at midnight is the last change before it; with none in the read,
    a first change of "not ready" means they were Ready since midnight; with no
    change at all, the user's current Ready."""
    rows = sorted(
        ((at, bool(r.get("is_ready"))) for r in ready_log
         if r.get("user_id") == user_id and (at := parse_ts(r.get("changed_at")))),
        key=lambda x: x[0],
    )
    before = [on for at, on in rows if at < day_start]
    today = [(at, on) for at, on in rows if at >= day_start]
    if before:
        state = before[-1]
    elif today:
        state = not today[0][1]
    else:
        state = fallback
    spans: list[list[Optional[str]]] = []
    start = day_start if state else None
    for at, on in today:
        if on and start is None:
            start = at
        elif not on and start is not None:
            spans.append([start.isoformat(), at.isoformat()])
            start = None
    if start is not None:
        spans.append([start.isoformat(), None])
    return spans


def tunnel_of(user: dict) -> Optional[str]:
    scope = user.get("tunnel_scope")
    if scope in OPERATOR_ROLES:
        return scope
    return user.get("role") if user.get("role") in OPERATOR_ROLES else None


def team_card(
    team: dict,
    *,
    users: list[dict],
    people: list[dict],
    ready_log: list[dict],
    takes: list[dict],
    day_start: datetime,
    now: datetime,
    names: dict[str, Optional[str]],
) -> dict:
    """A team on Teams & shifts: who's in it, what it did today, and how many
    of its people were Ready in each hour of the viewer's day so far."""
    members = [u for u in users if u.get("team_id") == team["id"] and u.get("role") in OPERATOR_ROLES]
    ids = {u["id"] for u in members}
    spans = {
        u["id"]: [
            (parse_ts(a), parse_ts(b) if b else now)
            for a, b in ready_spans(u["id"], ready_log, day_start, fallback=bool(u.get("is_ready")))
        ]
        for u in members
    }
    coverage: list[Optional[int]] = []
    for h in range(24):
        a, b = day_start + timedelta(hours=h), day_start + timedelta(hours=h + 1)
        if a >= now:
            coverage.append(None)
            continue
        coverage.append(sum(1 for ss in spans.values() if any(x and y and x < b and y > a for x, y in ss)))
    mine = [t for t in takes if t["user_id"] in ids]
    return {
        "id": team["id"], "name": team.get("name"),
        "shift_start": team.get("shift_start"), "shift_end": team.get("shift_end"),
        "supervisor_name": names.get(team.get("supervisor_id")) if team.get("supervisor_id") else None,
        "pm_name": names.get(team.get("pm_id")) if team.get("pm_id") else None,
        "members": [{"id": u["id"], "name": u.get("name")} for u in members],
        "ready_now": sum(1 for p in people if p["id"] in ids and p["presence"] == "ready"),
        "won_today": len({t["conversation_id"] for t in mine}),
        "back_today": len({t["conversation_id"] for t in mine if t["ended_by"] == "deadline_fired"}),
        "coverage": coverage,
    }


def build_team_now(
    *,
    now: datetime,
    offset_minutes: int,
    users: list[dict],
    teams: list[dict],
    conversations: list[dict],
    leads: list[dict],
    activity: list[dict],
    agent_msgs: list[dict],
    ready_log: list[dict],
    previews: Optional[dict[str, dict]] = None,
    team_ids: Optional[set[str]] = None,
    tunnel: Optional[str] = None,
    masked: bool = False,
    queue_window_s: int = 90,
    online_s: int = 600,
) -> dict:
    """The Now tab. `team_ids` set (supervisor, PM): their teams' people and
    the chats those people have; the queue and the AI's chats of their tunnel
    (`tunnel`, None = both) as counts and names they can't open. `masked`:
    clients' names masked and no message previews."""
    day_start = local_day_start(now, offset_minutes)
    team_by_id = {t["id"]: t for t in teams if t.get("id")}
    names = {u["id"]: u.get("name") for u in users if u.get("id")}
    people = [
        u for u in users
        if u.get("is_active", True) and u.get("role") in OPERATOR_ROLES
        and (team_ids is None or u.get("team_id") in team_ids)
    ]
    people_ids = {u["id"] for u in people}
    lead_of = {row["conversation_id"]: row for row in leads if row.get("conversation_id")}
    previews = previews or {}

    def in_tunnel(c: dict) -> bool:
        return tunnel is None or c.get("tunnel") == tunnel

    def chat(c: dict) -> dict:
        p = previews.get(c["id"]) or {}
        return {
            "id": c["id"], "chat_number": c.get("chat_number"), "tunnel": c.get("tunnel"),
            "client": client_label(c.get("visitor_name"), masked=masked),
            "route": route_of(lead_of.get(c["id"])), "messages": c.get("message_count"),
            "preview": None if masked else p.get("content"),
        }

    queue, with_person, ai_only = [], [], []
    for c in conversations:
        if c.get("status") not in ("active", "needs_agent"):
            continue
        agent = c.get("assigned_agent_id")
        if agent:
            if team_ids is not None and agent not in people_ids and c.get("team_id") not in team_ids:
                continue
            last_user = parse_ts(c.get("last_user_message_at"))
            last_agent = parse_ts(c.get("last_agent_message_at"))
            waiting = bool(last_user and (not last_agent or last_user > last_agent))
            with_person.append({
                **chat(c), "agent_id": agent, "agent_name": names.get(agent),
                "client_waiting": waiting,
                "since": (last_user if waiting else last_agent or parse_ts(c.get("created_at")) or now).isoformat(),
            })
        elif c.get("queued_at") and in_tunnel(c):
            queue.append({**chat(c), "since": c["queued_at"], "asked_for_person": c.get("status") == "needs_agent"})
        elif not c.get("queued_at") and in_tunnel(c):
            ai_only.append({**chat(c), "since": c.get("created_at")})
    queue.sort(key=lambda r: r["since"])
    # The client who has waited longest for a reply first, then the rest.
    with_person.sort(key=lambda r: (not r["client_waiting"], r["since"]))
    ai_only.sort(key=lambda r: r["since"] or "", reverse=True)

    # Today, from the take log.
    today_acts = [a for a in activity if (parse_ts(a.get("happened_at")) or now) >= day_start]
    takes = [t for t in build_takes(today_acts, agent_msgs, now) if t["user_id"] in people_ids]
    back_today = [t for t in takes if t["ended_by"] == "deadline_fired"]
    chats_now = defaultdict(int)
    for r in with_person:
        chats_now[r["agent_id"]] += 1

    person_rows = []
    for u in people:
        uid = u["id"]
        mine = [t for t in takes if t["user_id"] == uid]
        team = team_by_id.get(u.get("team_id") or "")
        person_rows.append({
            "id": uid, "name": u.get("name"), "role": u.get("role"), "tunnel": tunnel_of(u),
            "team_id": u.get("team_id"), "team_name": (team or {}).get("name"),
            "presence": presence_of(u, now, queue_window_s=queue_window_s, online_s=online_s),
            "ready_since": ready_since(uid, ready_log), "last_seen_at": u.get("last_seen_at"),
            "chats_now": chats_now.get(uid, 0),
            "took_today": len({t["conversation_id"] for t in mine}),
            "first_reply_today_s": _med(t["reply_s"] for t in mine),
            "went_back_today": len({t["conversation_id"] for t in mine if t["ended_by"] == "deadline_fired"}),
        })
    order = {"ready": 0, "no_signal": 1, "online": 2, "offline": 3}
    person_rows.sort(key=lambda p: (order[p["presence"]], -(p["chats_now"]), p["name"] or ""))
    by_id = {p["id"]: p for p in person_rows}

    # Needs attention: only past a line.
    attention: list[dict] = []
    for r in with_person:
        wait = (now - (parse_ts(r["since"]) or now)).total_seconds()
        if r["client_waiting"] and wait >= CLIENT_WAIT_LINE_S:
            attention.append({
                "kind": "client_waiting", "level": "urgent", "user_id": r["agent_id"], "name": r["agent_name"],
                "conversation_id": r["id"], "client": r["client"], "chat_number": r["chat_number"],
                "minutes": int(wait // 60),
            })
    for p in person_rows:
        if p["presence"] == "no_signal":
            seen = parse_ts(p["last_seen_at"])
            attention.append({
                "kind": "no_signal", "level": "check", "user_id": p["id"], "name": p["name"],
                "minutes": int((now - seen).total_seconds() // 60) if seen else None,
                "off_after_minutes": online_s // 60,
            })
    for t_name in ("sales", "support"):
        if tunnel and t_name != tunnel:
            continue
        line = [q for q in queue if q["tunnel"] == t_name]
        if not line:
            continue
        oldest = line[0]
        wait = (now - (parse_ts(oldest["since"]) or now)).total_seconds()
        if wait >= QUEUE_WAIT_LINE_S:
            ready = [p for p in person_rows if p["presence"] == "ready" and p["tunnel"] == t_name]
            attention.append({
                "kind": "queue_wait", "level": "check", "tunnel": t_name, "conversation_id": oldest["id"],
                "client": oldest["client"], "chat_number": oldest["chat_number"], "minutes": int(wait // 60),
                "ready": len(ready), "ready_chats": [p["chats_now"] for p in ready],
            })
    for t_name in ("sales", "support"):
        if tunnel and t_name != tunnel:
            continue
        crew = [p for p in person_rows if p["tunnel"] == t_name]
        ready = [p for p in crew if p["presence"] == "ready"]
        if len(crew) >= 2 and len(ready) == 1:
            attention.append({
                "kind": "coverage", "level": "check", "tunnel": t_name, "user_id": ready[0]["id"],
                "name": ready[0]["name"], "since": ready[0]["ready_since"],
            })
    seen_back: set[str] = set()
    for t in sorted(back_today, key=lambda t: t["end_at"] or t["at"], reverse=True):
        if t["user_id"] in seen_back or len(seen_back) >= WENT_BACK_SHOWN:
            continue
        seen_back.add(t["user_id"])
        p = by_id.get(t["user_id"], {})
        attention.append({
            "kind": "went_back", "level": "info", "user_id": t["user_id"], "name": names.get(t["user_id"]),
            "at": (t["end_at"] or t["at"]).isoformat(), "conversation_id": t["conversation_id"],
            "presence": p.get("presence"), "last_seen_at": p.get("last_seen_at"),
        })
    rank = {"urgent": 0, "check": 1, "info": 2}
    attention.sort(key=lambda a: rank[a["level"]])

    oldest_queue = queue[0] if queue else None
    return {
        "now": now.isoformat(),
        "day_start": day_start.isoformat(),
        "stats": {
            "ready": sum(1 for p in person_rows if p["presence"] == "ready"),
            "no_signal": sum(1 for p in person_rows if p["presence"] == "no_signal"),
            "online": sum(1 for p in person_rows if p["presence"] != "offline"),
            "waiting": len(queue),
            "oldest_waiting": {"client": oldest_queue["client"], "since": oldest_queue["since"]} if oldest_queue else None,
            "first_reply_today_s": _med(t["reply_s"] for t in takes),
            "replies_today": sum(1 for t in takes if t["reply_s"] is not None),
            "back_today": len({t["conversation_id"] for t in back_today}),
        },
        "attention": attention,
        "queue": queue,
        "with_person": with_person,
        "ai_only": {"count": len(ai_only), "chats": [] if team_ids is not None else ai_only[:20]},
        "people": person_rows,
        "teams": [
            team_card(t, users=users, people=person_rows, ready_log=ready_log, takes=takes,
                      day_start=day_start, now=now, names=names)
            for t in teams if t.get("id") and (team_ids is None or t.get("id") in team_ids)
        ],
        "windows": {"queue_s": queue_window_s, "ready_off_s": online_s},
    }


def build_person_day(
    *,
    now: datetime,
    offset_minutes: int,
    user: dict,
    team: Optional[dict],
    activity: list[dict],
    agent_msgs: list[dict],
    conversations: dict[str, dict],
    ready_log: list[dict],
    presence_days: list[dict],
    open_chats: list[dict],
    masked: bool,
    queue_window_s: int = 90,
    online_s: int = 600,
) -> dict:
    """One person's day for the panel on Team: today's numbers, when they
    were Ready, hours online over 7 days, and what they did today."""
    day_start = local_day_start(now, offset_minutes)
    uid = user["id"]
    acts = [a for a in activity if a.get("user_id") == uid]
    takes = build_takes([a for a in acts if a.get("action") != "claim_lost"], agent_msgs, now)
    takes = [t for t in takes if t["user_id"] == uid and t["at"] >= day_start]

    def who(cid: str) -> str:
        return client_label((conversations.get(cid) or {}).get("visitor_name"), masked=masked) or "a client"

    spans = ready_spans(uid, ready_log, day_start, fallback=bool(user.get("is_ready")))

    today_presence = next(
        (d for d in presence_days if d.get("day") == (now + timedelta(minutes=offset_minutes)).date().isoformat()),
        None,
    )
    week = []
    for n in range(6, -1, -1):
        day = (now + timedelta(minutes=offset_minutes) - timedelta(days=n)).date().isoformat()
        row = next((d for d in presence_days if d.get("day") == day), None)
        week.append({"date": day, "minutes_online": (row or {}).get("minutes_online") or 0})

    events: list[dict] = []
    for r in ready_log:
        at = parse_ts(r.get("changed_at"))
        if r.get("user_id") == uid and at and at >= day_start:
            events.append({"at": at.isoformat(), "kind": "ready" if r.get("is_ready") else "not_ready"})
    for t in takes:
        cid = t["conversation_id"]
        events.append({"at": t["at"].isoformat(), "kind": "took", "conversation_id": cid, "client": who(cid)})
        if t["reply_s"] is not None:
            events.append({"at": (t["at"] + timedelta(seconds=t["reply_s"])).isoformat(), "kind": "replied",
                           "conversation_id": cid, "client": who(cid), "seconds": t["reply_s"]})
        if t["ended_by"] == "deadline_fired" and t["end_at"]:
            events.append({"at": t["end_at"].isoformat(), "kind": "went_back", "conversation_id": cid, "client": who(cid)})
        elif t["ended_by"] == "closed" and t["end_at"]:
            events.append({"at": t["end_at"].isoformat(), "kind": "closed", "conversation_id": cid, "client": who(cid)})
    lost = [a for a in acts if a.get("action") == "claim_lost" and (parse_ts(a.get("happened_at")) or now) >= day_start]
    for a in lost:
        cid = a.get("conversation_id")
        events.append({"at": a["happened_at"], "kind": "take_lost", "conversation_id": cid, "client": who(cid)})
    for c in open_chats:
        last_user = parse_ts(c.get("last_user_message_at"))
        last_agent = parse_ts(c.get("last_agent_message_at"))
        if last_user and (not last_agent or last_user > last_agent):
            events.append({"at": last_user.isoformat(), "kind": "client_waiting", "conversation_id": c["id"],
                           "client": client_label(c.get("visitor_name"), masked=masked) or "a client", "live": True})
    events.sort(key=lambda e: parse_ts(e["at"]) or now, reverse=True)

    return {
        "user": {
            "id": uid, "name": user.get("name"), "email": user.get("email"), "role": user.get("role"),
            "tunnel": tunnel_of(user), "is_active": user.get("is_active"),
            "team": {"id": team["id"], "name": team.get("name"), "shift_start": team.get("shift_start"),
                     "shift_end": team.get("shift_end")} if team else None,
            "presence": presence_of(user, now, queue_window_s=queue_window_s, online_s=online_s),
            "ready_since": ready_since(uid, ready_log), "last_seen_at": user.get("last_seen_at"),
        },
        "today": {
            "chats_now": len(open_chats),
            "took": len({t["conversation_id"] for t in takes}),
            "first_reply_median_s": _med(t["reply_s"] for t in takes),
            "replied_to": len({t["conversation_id"] for t in takes if t["reply_s"] is not None}),
            "went_back": len({t["conversation_id"] for t in takes if t["ended_by"] == "deadline_fired"}),
            "takes_lost": len(lost),
            "minutes_online": (today_presence or {}).get("minutes_online") or 0,
            "first_seen_at": (today_presence or {}).get("first_seen_at"),
        },
        "ready_today": spans,
        "week_online": week,
        "activity": events,
        "day_start": day_start.isoformat(),
    }
