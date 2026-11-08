"""Tasks: the rows, states and numbers the Tasks page shows. Pure.

Times go out as ISO instants with the business zone's name; the panel
writes them in that zone ("New York time") and, when the trip's departure
city is known, in the client's city too ("13:40 in Chicago").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.services.agent_day import parse_ts
from app.services.airport_tz import city_and_zone
from app.services.follow_ups import KIND_LABEL, zone
from app.services.to_read import client_label, mask_contacts, route_of

SOURCE_LABEL = {
    "caught": "Caught in chat",
    "client_asked": "Client asked",
    "added": "Added",
    "dashboard": "From Dashboard",
}


def week_start(now: datetime, tz) -> datetime:
    """Monday 00:00 of this week in the business zone, as UTC."""
    local = now.astimezone(tz)
    monday = local.date() - timedelta(days=local.weekday())
    return datetime.combine(monday, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)


def state_of(due: Optional[datetime], now: datetime, tz) -> str:
    """"late" | "today" | "later" — today is the business zone's day."""
    if due is None:
        return "later"
    if due < now:
        return "late"
    return "today" if due.astimezone(tz).date() == now.astimezone(tz).date() else "later"


def _first_name(name: Any) -> str:
    return name.strip().split()[0] if isinstance(name, str) and name.strip() else ""


def task_row(
    t: dict,
    *,
    conv: Optional[dict],
    lead: Optional[dict],
    names: dict[str, str],
    wrote_at: Optional[str],
    now: datetime,
    tz,
    masked: bool,
) -> dict:
    """One task as the list shows it. `masked`: the reader sees clients' contacts masked."""
    name = (conv or {}).get("visitor_name")
    label = client_label(name, masked=masked) if name else ""
    title = t.get("title") or ""
    first = _first_name(name)
    if masked and first and label:
        title = title.replace(first, label)
    origin = (lead or {}).get("origin_code")
    city_zone = city_and_zone(origin)
    due = parse_ts(t.get("due_at"))
    return {
        "id": t["id"],
        "number": t.get("task_number"),
        "title": title,
        "kind": t.get("kind") or "todo",
        "kind_label": KIND_LABEL.get(t.get("kind") or "todo", "To-do"),
        "source": t.get("source") or "added",
        "source_label": SOURCE_LABEL.get(t.get("source") or "added", "Added"),
        "quote": mask_contacts(t.get("quote")) if masked else t.get("quote"),
        "conversation_id": t.get("conversation_id"),
        "lead_id": t.get("lead_id") or (lead or {}).get("id"),
        "chat_number": (conv or {}).get("chat_number"),
        "client": label or None,
        "route": route_of(lead),
        "departure_date": (lead or {}).get("departure_date"),
        "passengers": (lead or {}).get("passengers"),
        "children_count": (lead or {}).get("children_count"),
        "infant_count": (lead or {}).get("infant_count"),
        "client_city": city_zone[0] if city_zone else None,
        "client_zone": city_zone[1] if city_zone and zone(city_zone[1]) is not None else None,
        "due_at": due.isoformat() if due else None,
        "due_kind": t.get("due_kind") or "picked",
        "state": state_of(due, now, tz) if not (t.get("done_at") or t.get("dismissed_at")) else "done",
        "assignee_id": t.get("assignee_id"),
        "assignee_name": names.get(t["assignee_id"]) if t.get("assignee_id") else None,
        "unowned_reason": None if t.get("assignee_id") else t.get("unowned_reason"),
        "unowned_from_name": names.get(t["unowned_from"]) if t.get("unowned_from") and not t.get("assignee_id") else None,
        "unowned_at": t.get("unowned_at") if not t.get("assignee_id") else None,
        "created_by_name": names.get(t["created_by"]) if t.get("created_by") and t.get("source") in ("added", "dashboard") else None,
        "client_wrote_at": wrote_at,
        "created_at": t.get("created_at"),
        "done_at": t.get("done_at"),
        "done_by_name": names.get(t["done_by"]) if t.get("done_by") else None,
    }


def build_tasks_view(
    *,
    open_rows: list[dict],
    done_rows: list[dict],
    convs: dict[str, dict],
    leads: dict[str, dict],
    client_msgs: list[dict],
    names: dict[str, str],
    now: datetime,
    rules: dict,
    masked: bool,
) -> dict:
    """The page: open tasks (with their state), done this week, and the numbers."""
    tz = zone(rules["time_zone"]) or timezone.utc
    last_client: dict[str, list[datetime]] = {}
    for m in client_msgs:
        at = parse_ts(m.get("created_at"))
        if at and m.get("conversation_id"):
            last_client.setdefault(m["conversation_id"], []).append(at)

    def wrote_after(t: dict) -> Optional[str]:
        made = parse_ts(t.get("created_at"))
        cid = t.get("conversation_id")
        if not made or not cid:
            return None
        later = [a for a in last_client.get(cid, []) if a > made]
        return max(later).isoformat() if later else None

    def row(t: dict) -> dict:
        cid = t.get("conversation_id") or ""
        return task_row(t, conv=convs.get(cid), lead=leads.get(cid), names=names, wrote_at=wrote_after(t),
                        now=now, tz=tz, masked=masked)

    tasks = [row(t) for t in open_rows]
    done = [row(t) for t in done_rows if not t.get("dismissed_at")]
    counts = {"late": 0, "today": 0, "later": 0, "unowned": 0}
    for r in tasks:
        counts[r["state"]] += 1
        if not r["assignee_id"]:
            counts["unowned"] += 1

    # On time this week: of the tasks due since Monday (up to now), done by their time.
    start = week_start(now, tz)
    due_this_week = 0
    on_time = 0
    for t in [*open_rows, *done_rows]:
        if t.get("dismissed_at"):
            continue
        due = parse_ts(t.get("due_at"))
        if not due or not (start <= due <= now):
            continue
        due_this_week += 1
        finished = parse_ts(t.get("done_at"))
        if finished and finished <= due:
            on_time += 1
    return {
        "tasks": tasks,
        "done": done,
        "counts": counts,
        "on_time": {"on_time": on_time, "due": due_this_week},
        "time_zone": rules["time_zone"],
        "day_end": rules["day_end"],
        "now": now.isoformat(),
    }


# ── One task: where it came from, and what happened to it ───────────────────

EVENT_TEXT = {
    "caught": "Caught in {actor}’s message.",
    "client_asked": "{client} asked to be called, in a chat with {with_}.",
    "added": "Added by {actor}.",
    "dashboard": "Made from the Dashboard by {actor}.",
    "due_changed": "{actor} moved it to {due}.",
    "given": "{actor} gave it to {to}.",
    "unowned": "Went to Unowned: {why}.",
    "given_ready": "Given to {to}, who was Ready, when {from_} went Not ready.",
    "done": "{actor} marked it done.",
    "reopened": "{actor} opened it again.",
    "dismissed": "{actor} said it was not a promise.",
    "edited": "{actor} changed it.",
}


def when_text(at: Optional[datetime], tz) -> str:
    """"Mon 9 Nov, 10:00" in the business zone."""
    if not at:
        return "a new time"
    local = at.astimezone(tz)
    return f"{local.strftime('%a')} {local.day} {local.strftime('%b')}, {local.strftime('%H:%M')}"


def history_of(events: list[dict], names: dict[str, str], *, client: str, tz=timezone.utc) -> list[dict]:
    """The task's events in plain sentences, oldest first."""
    out: list[dict] = []
    for e in events:
        d = e.get("details") or {}
        actor = names.get(e.get("actor_id") or "", "") or "Someone"
        text = EVENT_TEXT.get(e.get("kind") or "", "")
        if not text:
            continue
        why = "the chat was only with the AI" if d.get("reason") == "ai_only" else (
            f"{names.get(d.get('from') or '', 'they')} went Not ready" if d.get("reason") == "left_shift" else "nobody had it"
        )
        line = text.format(
            actor=actor, client=client or "The client", with_=d.get("with") or "the AI",
            due=when_text(parse_ts(d.get("due_at")), tz), to=names.get(d.get("to") or "", "someone"),
            from_=names.get(d.get("from") or "", "they"), why=why,
        )
        if e.get("kind") in ("caught", "client_asked") and d.get("due_words"):
            line += f" Due {d['due_words']}, as written." if d.get("due_kind") == "said" else ""
        if e.get("kind") in ("caught", "client_asked") and d.get("due_kind") == "default":
            line += " No time was said, so the default."
        out.append({"at": e.get("at"), "kind": e.get("kind"), "text": line})
    return out


def excerpt_of(
    messages: list[dict],
    *,
    task: dict,
    client_first: str,
    agent_name: Optional[str],
    masked: bool,
) -> list[dict]:
    """The chat around the task's message: who wrote what, which line is the
    promise, which client messages came after it."""
    made = parse_ts(task.get("created_at"))
    out = []
    for m in messages:
        role = m.get("role")
        who = (client_first or "Client") if role == "user" else (agent_name or "Agent") if role == "agent" else "Assistant"
        at = parse_ts(m.get("created_at"))
        out.append({
            "id": m.get("id"),
            "role": role,
            "who": who,
            "at": m.get("created_at"),
            "text": mask_contacts(m.get("content")) if masked else m.get("content"),
            "source": bool(task.get("message_id")) and m.get("id") == task.get("message_id"),
            "after": bool(role == "user" and at and made and at > made),
        })
    return out
