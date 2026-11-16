"""The departure task: a client who leaves after giving a contact, with nobody
on the chat, becomes a task for the free agent (ADR 09 of the proactive-chat
debate, 15 Nov 2026 — wave 2).

Every minute (app/services/scheduler.py, job "departure_tasks"), an open
sales chat gets one task when all of these hold:
- the client wrote at least one message;
- the chat has a phone or an email;
- the presence rule says the client left (client_presence.client_left: 90 s
  without another page, 2 min with the phone in the background, 20 min idle),
  at most 30 minutes ago — past that, the abandoned-lead job has the lead;
- nobody is on the chat (not in human mode, nobody assigned);
- the chat has no open task and never had a departure task;
- its lead is not in the CRM yet (the client confirmed the summary);
- it is not held by the form trap or the behaviour lock (a bot is no lead).

The task: a call when the chat has a phone (the client left it, in the form
or in the chat), else an email; due 15 minutes after the client left; given
to the Ready sales agent with the fewest open tasks (the leave_shift rule),
or Unowned with the reason "no_ready_agent" when nobody is Ready.

It never calls, texts or e-mails anyone itself. Off at merge:
DEPARTURE_TASKS_ENABLED (app_settings.flags, read every 30 s). Needs
migration 053 (the new source and Unowned reason); before it the database
refuses the row, and every refusal is counted (tasks.departure_failures).
No names, phones or e-mails in the logs: chats by id only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.db import supabase as db
from app.services.client_presence import client_left
from app.services.to_read import route_of

logger = logging.getLogger(__name__)

# Due this long after the client left: they are still near their phone.
DUE_AFTER = timedelta(minutes=15)
# Left longer ago than this: the abandoned-lead job (30 min) takes the lead.
MAX_LEFT_SECONDS = 30 * 60
SOURCE = "departure"
UNOWNED_REASON = "no_ready_agent"


def enabled() -> bool:
    from app.flags import flag
    return bool(flag("DEPARTURE_TASKS_ENABLED", False))


def serves_sales(user: dict) -> bool:
    """A Ready agent who takes sales chats: scope sales or all, or no scope and the sales role."""
    scope = user.get("tunnel_scope")
    if scope:
        return scope in ("sales", "all")
    return user.get("role") == "sales"


def _first_name(name) -> str:
    return name.strip().split()[0] if isinstance(name, str) and name.strip() else ""


def departure_title(kind: str, first_name: Optional[str], route: Optional[str]) -> str:
    """"Call Maria back about New York → London" / "Email Maria about …"."""
    who = (first_name or "").strip() or "the client"
    if kind == "call":
        return f"Call {who} back about {route}" if route else f"Call {who} back"
    return f"Email {who} about {route}" if route else f"Email {who} back"


def _present(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


def seconds_since_left(
    conv: dict,
    lead: Optional[dict],
    tasks: list[dict],
    now: datetime,
) -> Optional[int]:
    """Seconds since the client left, when this chat gets a departure task; None otherwise.

    `tasks`: every task of this chat (open or not)."""
    if not conv.get("last_user_message_at"):
        return None  # the client never wrote: no conversation to follow up
    if not (_present(conv.get("visitor_phone")) or _present(conv.get("visitor_email"))):
        return None
    if conv.get("mode") == "human" or conv.get("assigned_agent_id"):
        return None
    if (lead or {}).get("created_in_crm"):
        return None
    meta = conv.get("metadata") if isinstance(conv.get("metadata"), dict) else {}
    from app.security.behavior import is_locked
    # The form trap's mark, read as it is: holds_crm() would count a held CRM
    # push (form_trap.crm_held) every minute this job looks at the chat.
    if meta.get("suspect") == "form_trap" or is_locked(meta):
        return None
    for t in tasks:
        if t.get("source") == SOURCE:
            return None  # at most one departure task per chat, ever
        if not t.get("done_at") and not t.get("dismissed_at"):
            return None  # someone already has something open on it
    gone = client_left(meta, conv.get("last_user_message_at"), now)
    if gone is None or gone > MAX_LEFT_SECONDS:
        return None
    return int(gone)


class _Assigner:
    """The Ready sales agent with the fewest open tasks, counting the tasks
    this run gives out, so a burst of departures is spread, not piled. A task
    counts once it is made (took), not when its agent is picked: a refused
    insert leaves the agent's count as it was."""

    def __init__(self, ready_ids: list[str], counts: dict[str, int]):
        self.ready_ids = ready_ids
        self.counts = dict(counts)

    def pick(self) -> Optional[str]:
        if not self.ready_ids:
            return None
        return min(self.ready_ids, key=lambda i: (self.counts.get(i, 0), i))

    def took(self, uid: str) -> None:
        self.counts[uid] = self.counts.get(uid, 0) + 1


async def _assigner() -> _Assigner:
    ready = [u["id"] for u in await db.ready_operators() if u.get("id") and serves_sales(u)]
    counts = await db.open_task_counts(ready) if ready else {}
    return _Assigner(ready, counts)


async def run_departure_tasks() -> dict:
    """The job: one task per chat whose client left with a contact. Never raises."""
    if not enabled():
        return {"enabled": False}
    from app.services.tasks_service import note, what_failed

    created = unowned = 0
    try:
        # One row per chat: "at most one departure task per chat" is checked against the tasks read
        # below, before any insert, so a chat read twice would get two.
        convs = list({c["id"]: c for c in await db.get_departure_candidates() if c.get("id")}.values())
        if not convs:
            return {"candidates": 0, "created": 0}
        ids = [c["id"] for c in convs if c.get("id")]
        tasks = await db.tasks_of_conversations(ids)
        _, leads = await db.get_follow_up_context(ids)
    except db.TasksNotSetUp:  # noqa: silent — migration 051 not applied: _tasks_call logged it and counted it (tasks.schema_missing); nothing to make tasks in
        return {"tasks_not_set_up": True}
    except Exception as e:
        db.TASKS_HEALTH["departure_failures"] += 1
        logger.error(f"[departure] reading the open sales chats failed: {what_failed(e)}")
        return {"failed": "read"}

    by_conv: dict[str, list[dict]] = {}
    for t in tasks:
        by_conv.setdefault(t.get("conversation_id"), []).append(t)
    now = datetime.now(timezone.utc)
    due = [(c, gone) for c in convs
           if (gone := seconds_since_left(c, leads.get(c["id"]), by_conv.get(c["id"], []), now)) is not None]
    if not due:
        return {"candidates": len(convs), "created": 0}

    try:
        assign = await _assigner()
    except Exception as e:
        # Who is Ready can't be read: the tasks still go out, Unowned, for the supervisor.
        db.TASKS_HEALTH["departure_failures"] += 1
        logger.error(f"[departure] reading who is Ready failed, tasks go Unowned: {what_failed(e)}")
        assign = _Assigner([], {})

    for conv, gone in due:
        cid = conv["id"]
        # Read again just before the insert: an agent may have taken the chat (or it closed) since the
        # candidates were read. A narrower window, not none. A second departure task for the chat is
        # the database's to refuse (uq_tasks_departure, migration 053): two runs can overlap during a
        # deploy, when the old and the new process each run the scheduler.
        try:
            free = await db.departure_still_free(cid)
        except Exception as e:
            db.TASKS_HEALTH["departure_failures"] += 1
            logger.error(f"[departure] re-reading {cid} before its task failed, tried again next run: {what_failed(e)}")
            continue
        if not free:
            logger.info(f"[departure] {cid} was taken or closed since the read: no task")
            continue
        lead = leads.get(cid) or {}
        kind = "call" if _present(conv.get("visitor_phone")) else "email"
        owner = assign.pick()
        left_at = now - timedelta(seconds=gone)
        try:
            task = await db.insert_follow_up({
                "title": departure_title(kind, _first_name(conv.get("visitor_name")), route_of(lead)),
                "kind": kind,
                "source": SOURCE,
                "conversation_id": cid,
                "lead_id": lead.get("id"),
                "due_at": (left_at + DUE_AFTER).isoformat(),
                "due_kind": "default",
                "assignee_id": owner,
                "unowned_reason": None if owner else UNOWNED_REASON,
                "unowned_at": None if owner else now.isoformat(),
            })
        except db.TasksNotSetUp:  # noqa: silent — migration 051 not applied: _tasks_call logged it and counted it in /health
            return {"tasks_not_set_up": True, "created": created}
        except Exception as e:
            # Before migration 053 the database refuses the new source: counted, and the next chat is tried.
            db.TASKS_HEALTH["departure_failures"] += 1
            logger.error(f"[departure] the task for {cid} wasn't made: {what_failed(e)}")
            continue
        if not task:
            # No row back: either another run made this chat's task first (the unique index refused
            # this one, which insert_follow_up returns as None), or the database answered without the
            # row. Read which, so only the second is counted.
            try:
                made_elsewhere = any(t.get("source") == SOURCE for t in await db.tasks_of_conversations([cid]))
            except Exception as e:
                db.TASKS_HEALTH["departure_failures"] += 1
                logger.error(f"[departure] the task for {cid} wasn't made, and reading why failed: {what_failed(e)}")
                continue
            if made_elsewhere:
                logger.info(f"[departure] {cid} got its task from another run at the same time: not made twice")
                continue
            db.TASKS_HEALTH["departure_failures"] += 1
            logger.error(f"[departure] the task for {cid} wasn't made: the insert returned no row")
            continue
        created += 1
        db.TASKS_HEALTH["departure"] += 1
        if owner:
            assign.took(owner)
        else:
            unowned += 1
            db.TASKS_HEALTH["departure_unowned"] += 1
        await note(task["id"], "departure", None, {
            "kind": kind, "left_minutes_ago": round(gone / 60), "given": "ready" if owner else "unowned",
            # The Tasks page says when it was due from this, not from today's constant.
            "due_minutes": int(DUE_AFTER.total_seconds() // 60),
        })
        logger.info(f"[departure] {kind} task for {cid}: {'given to a Ready agent' if owner else 'Unowned, nobody Ready'}")
    return {"candidates": len(convs), "due": len(due), "created": created, "unowned": unowned}
