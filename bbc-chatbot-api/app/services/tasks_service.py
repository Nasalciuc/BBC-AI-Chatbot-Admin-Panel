"""Tasks that make themselves: a promise in an agent's message, a client's
"call me" while the AI has the chat, and the tasks of someone who goes Not
ready. Every entry point here never raises into its caller: a send, a
pipeline turn or a Ready switch must not fail because of a task. Failures are
logged and counted in /health (TASKS_HEALTH in app/db/supabase.py).
"""

from __future__ import annotations

import logging
import os
import time as _time
import traceback
from datetime import datetime, timezone
from typing import Optional

from app.db import supabase as db
from app.services.agent_day import parse_ts
from app.services.airport_tz import city_and_zone
from app.services.follow_ups import catch_promise, due_from, rules_of, title_for
from app.services.to_read import route_of

logger = logging.getLogger(__name__)

_RULES_TTL = 60.0
_rules_cache: Optional[tuple[float, dict]] = None
# Before migration 051 every agent message would ask the database again;
# "not set up" is remembered for the same minute.
_not_set_up_at: Optional[float] = None


async def load_rules() -> dict:
    """The Tasks → Rules, cached for a minute. Raises TasksNotSetUp before migration 051."""
    global _rules_cache, _not_set_up_at
    now = _time.monotonic()
    if _rules_cache and now - _rules_cache[0] < _RULES_TTL:
        return _rules_cache[1]
    if _not_set_up_at is not None and now - _not_set_up_at < _RULES_TTL:
        db.TASKS_HEALTH["schema_missing"] += 1
        raise db.TasksNotSetUp("migration 051 not applied (checked less than a minute ago)")
    try:
        rules = rules_of(await db.get_task_rules_raw())
    except db.TasksNotSetUp:
        _not_set_up_at = now
        raise
    _not_set_up_at = None
    _rules_cache = (now, rules)
    return rules


def forget_rules() -> None:
    """After a save on Tasks → Rules (or in tests): the next read asks the database."""
    global _rules_cache, _not_set_up_at
    _rules_cache = None
    _not_set_up_at = None


def _first(name) -> str:
    return name.strip().split()[0] if isinstance(name, str) and name.strip() else ""


_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def what_failed(e: BaseException) -> str:
    """What failed, never what was in it — for the ERROR logs around a
    task's row. A database error can quote the row it refused ("Failing row
    contains (…)"), and a task's title carries the client's first name, its
    quote an e-mail or a phone. So: the error's type, the database's code,
    and where in our code it came from ("APIError 23514 at tasks_service.py:84
    → supabase.py:3383"). No message, no traceback text."""
    code = getattr(e, "code", None)
    ours = [f"{os.path.basename(f.filename)}:{f.lineno}" for f in traceback.extract_tb(e.__traceback__)
            if os.path.abspath(f.filename).startswith(_APP_DIR + os.sep)]
    where = f" at {' → '.join(dict.fromkeys([ours[0], ours[-1]]))}" if ours else ""
    return f"{type(e).__name__}{f' {code}' if isinstance(code, (str, int)) and code != '' else ''}{where}"


async def note(task_id: str, kind: str, actor_id: Optional[str], details: Optional[dict] = None) -> None:
    """One line of a task's history, written after the task itself is saved.
    A line that can't be written is logged and counted (tasks.event_failures
    in /health), never the task's failure: the task is made or changed, and
    an answer of "couldn't add" would have the person add it a second time."""
    try:
        await db.add_task_event(task_id, kind, actor_id, details)
    except Exception as e:
        db.TASKS_HEALTH["event_failures"] += 1
        logger.error(f"[tasks] task {task_id}: its {kind} line wasn't written: {what_failed(e)}")


async def catch_from_agent_message(conv: dict, msg: dict, sender_id: str) -> Optional[dict]:
    """The task an agent's message makes (a promise in the rules' phrases), or None.

    One task per message: catching it again (a retry) makes nothing new."""
    cid = conv.get("id")
    try:
        rules = await load_rules()
        if not rules["catch_promises"] or not msg or not msg.get("id"):
            return None
        caught = catch_promise(msg.get("content") or "", rules["phrases"])
        if not caught:
            return None
        _, leads = await db.get_follow_up_context([cid])
        lead = leads.get(cid)
        place = city_and_zone((lead or {}).get("origin_code"))
        sent_at = parse_ts(msg.get("created_at")) or datetime.now(timezone.utc)
        due = due_from(caught.sentence, sent_at, rules, client_zone=place[1] if place else None)
        task = await db.insert_follow_up({
            "title": title_for(caught.kind, caught.sentence, _first(conv.get("visitor_name")), route_of(lead)),
            "kind": caught.kind,
            "source": "caught",
            "conversation_id": cid,
            "lead_id": (lead or {}).get("id"),
            "message_id": msg["id"],
            "quote": caught.sentence,
            "due_at": due.at.isoformat(),
            "due_kind": due.kind,
            "assignee_id": sender_id,
            "created_by": sender_id,
        })
        if task is None:
            return None
        db.TASKS_HEALTH["caught"] += 1
        await note(task["id"], "caught", sender_id, {"phrase": caught.phrase, "due_words": due.words, "due_kind": due.kind})
        return task
    except db.TasksNotSetUp:  # noqa: silent — migration 051 not applied yet: _tasks_call logged it and counted it in /health (tasks.schema_missing); the message itself is already sent
        return None
    except Exception as e:
        db.TASKS_HEALTH["catch_failures"] += 1
        logger.error(f"[tasks] catching a promise in {cid} failed: {what_failed(e)}")
        return None


async def task_from_callback(conv: dict, message: dict, text: str) -> Optional[dict]:
    """A client asked to be called while the AI had the chat: a call task for
    the person on the chat, or Unowned when nobody is ("the chat was only
    with the AI"). The AI already told the client a consultant will call."""
    cid = conv.get("id")
    try:
        rules = await load_rules()
        if not message or not message.get("id"):
            return None
        _, leads = await db.get_follow_up_context([cid])
        lead = leads.get(cid)
        place = city_and_zone((lead or {}).get("origin_code"))
        asked_at = parse_ts(message.get("created_at")) or datetime.now(timezone.utc)
        due = due_from(text, asked_at, rules, client_zone=place[1] if place else None)
        owner = conv.get("assigned_agent_id") if conv.get("mode") == "human" else None
        task = await db.insert_follow_up({
            "title": title_for("call", text, _first(conv.get("visitor_name")), route_of(lead), client_asked=True),
            "kind": "call",
            "source": "client_asked",
            "conversation_id": cid,
            "lead_id": (lead or {}).get("id"),
            "message_id": message["id"],
            "quote": (text or "").strip()[:500],
            "due_at": due.at.isoformat(),
            "due_kind": due.kind,
            "assignee_id": owner,
            "unowned_reason": None if owner else "ai_only",
            "unowned_at": None if owner else datetime.now(timezone.utc).isoformat(),
        })
        if task is None:
            return None
        db.TASKS_HEALTH["client_asked"] += 1
        await note(task["id"], "client_asked", None,
                   {"with": "an agent" if owner else "the AI", "due_words": due.words, "due_kind": due.kind})
        return task
    except db.TasksNotSetUp:  # noqa: silent — migration 051 not applied yet: _tasks_call logged it and counted it in /health (tasks.schema_missing); the AI already answered the client
        return None
    except Exception as e:
        db.TASKS_HEALTH["catch_failures"] += 1
        logger.error(f"[tasks] call-back task for {cid} failed: {what_failed(e)}")
        return None


async def leave_shift(user_id: str) -> int:
    """Someone went Not ready: their open tasks go to Unowned (the supervisor
    gives them out), or to the Ready agent with the fewest open tasks when the
    rules say so. Returns how many moved."""
    try:
        rules = await load_rules()
        ids = await db.open_task_ids_of(user_id)
        if not ids:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        target: Optional[str] = None
        if rules["leave_shift"] == "ready":
            ready = [u["id"] for u in await db.ready_operators() if u.get("id") and u["id"] != user_id]
            if ready:
                counts = await db.open_task_counts(ready)
                target = min(ready, key=lambda uid: (counts.get(uid, 0), uid))
        for tid in ids:
            if target:
                await db.update_follow_up(tid, {"assignee_id": target, "unowned_reason": None, "unowned_from": None, "unowned_at": None})
                await note(tid, "given_ready", None, {"from": user_id, "to": target})
            else:
                await db.update_follow_up(tid, {"assignee_id": None, "unowned_reason": "left_shift",
                                                "unowned_from": user_id, "unowned_at": now})
                await note(tid, "unowned", None, {"reason": "left_shift", "from": user_id})
        db.TASKS_HEALTH["moved_to_ready" if target else "moved_unowned"] += len(ids)
        return len(ids)
    except db.TasksNotSetUp:  # noqa: silent — migration 051 not applied yet: there are no follow-up tasks to move; _tasks_call logged it and counted it in /health
        return 0
    except Exception as e:
        db.TASKS_HEALTH["move_failures"] += 1
        logger.error(f"[tasks] moving {user_id}'s tasks after Not ready failed: {what_failed(e)}")
        return 0
