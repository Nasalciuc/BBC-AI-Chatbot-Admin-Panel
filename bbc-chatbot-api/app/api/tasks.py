"""Tasks: follow-ups an agent promised a client, a client asked for, or
someone added (design canvas "Tasks · the new feature to build").

Who sees what:
  - sales, support, QA: their own tasks;
  - supervisor, project manager: theirs, their team's and Unowned, with
    clients' contacts masked as everywhere else in their panel;
  - owner, admin, dev: everyone's, and Tasks → Rules.
Until migration 051 runs every endpoint answers 503 "Tasks aren't set up
yet", never an empty list that reads as "no tasks". A failed read is 502.
"""

import logging
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import supabase as db
from app.security.auth import get_current_user
from app.services import tasks_service
from app.services.agent_day import parse_ts
from app.services.follow_ups import DEFAULT_DUES, LEAVE_SHIFT, rules_of, try_sentence, zone
from app.services.tasks_view import build_tasks_view, excerpt_of, history_of, task_row, week_start
from app.services.to_read import client_label, mask_contacts

logger = logging.getLogger(__name__)
router = APIRouter()

MANAGERS = {"owner", "admin", "dev"}
TEAM_LEADS = {"supervisor", "project_manager"}
NOT_SET_UP = "Tasks aren’t set up yet: migration 051 hasn’t run."


async def _reach(user: dict) -> dict:
    """Whose tasks this reader works with. members None = everyone's."""
    role = user.get("role") or "sales"
    uid = user.get("id") or ""
    if role in MANAGERS:
        return {"role": role, "id": uid, "masked": False, "members": None, "has_team": True}
    if role in TEAM_LEADS:
        lookup = db.get_team_ids_for_supervisor if role == "supervisor" else db.get_team_ids_for_pm
        team_ids = await lookup(uid) or []
        members = await db.get_operator_ids_for_teams(team_ids) if team_ids else []
        return {"role": role, "id": uid, "masked": True, "members": sorted(set(members) | {uid}), "has_team": bool(team_ids)}
    return {"role": role, "id": uid, "masked": False, "members": [uid], "has_team": False}


def _can_see(reach: dict, task: dict) -> bool:
    if reach["members"] is None:
        return True
    owner = task.get("assignee_id")
    if owner and owner in reach["members"]:
        return True
    if not owner and reach["role"] in TEAM_LEADS:
        return True
    return task.get("created_by") == reach["id"]


def _can_assign(reach: dict) -> bool:
    return reach["role"] in MANAGERS or reach["role"] in TEAM_LEADS


def _iso(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    at = parse_ts(value)
    if not at:
        raise HTTPException(400, "Due time must be an ISO date and time")
    return at.astimezone(timezone.utc).isoformat()


# ── Rules (owner, admin, dev) — declared before /tasks/{task_id} ─────────────

class RulesIn(BaseModel):
    catch_promises: Optional[bool] = None
    default_due: Optional[Literal[DEFAULT_DUES]] = None  # type: ignore[valid-type]
    leave_shift: Optional[Literal[LEAVE_SHIFT]] = None  # type: ignore[valid-type]
    day_start: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    day_end: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    time_zone: Optional[str] = None
    phrases: Optional[list[str]] = None


class TryIn(BaseModel):
    sentence: str = Field(..., min_length=1, max_length=500)


def _managers_only(user: dict) -> None:
    if (user.get("role") or "") not in MANAGERS:
        raise HTTPException(403, "Only an owner, admin or developer changes the task rules")


@router.get("/tasks/rules")
async def get_rules(user: dict = Depends(get_current_user)):
    _managers_only(user)
    from config.settings import settings

    now = datetime.now(timezone.utc)
    try:
        rules = await tasks_service.load_rules()
        week = await db.caught_stats_since((now - _days(7)).isoformat())
    except db.TasksNotSetUp:
        raise HTTPException(503, NOT_SET_UP)
    except Exception as e:
        logger.error(f"[tasks] rules read failed: {e}", exc_info=True)
        raise HTTPException(502, "Couldn't load the task rules")
    return {
        "success": True,
        "data": {**rules, "last_7_days": week, "left_after_minutes": settings.agent_timeout_seconds // 60},
        "count": 1,
    }


@router.put("/tasks/rules")
async def put_rules(body: RulesIn, user: dict = Depends(get_current_user)):
    _managers_only(user)
    patch = body.model_dump(exclude_none=True)
    if "time_zone" in patch and zone(patch["time_zone"]) is None:
        raise HTTPException(400, "Unknown time zone")
    try:
        current = await tasks_service.load_rules()
        rules = rules_of({**current, **patch})
        if "phrases" in patch and not rules["phrases"] and rules["catch_promises"]:
            raise HTTPException(400, "Keep at least one phrase, or turn catching off")
        await db.save_task_rules(rules)
        tasks_service.forget_rules()
    except HTTPException:
        raise
    except db.TasksNotSetUp:
        raise HTTPException(503, NOT_SET_UP)
    except Exception as e:
        logger.error(f"[tasks] rules save failed: {e}", exc_info=True)
        raise HTTPException(502, "Couldn't save the task rules")
    return {"success": True, "data": rules, "count": 1}


@router.post("/tasks/rules/try")
async def try_rules(body: TryIn, user: dict = Depends(get_current_user)):
    _managers_only(user)
    try:
        rules = await tasks_service.load_rules()
    except db.TasksNotSetUp:
        raise HTTPException(503, NOT_SET_UP)
    except Exception as e:
        logger.error(f"[tasks] rules read for a try failed: {e}", exc_info=True)
        raise HTTPException(502, "Couldn't load the task rules")
    return {"success": True, "data": try_sentence(body.sentence, rules, datetime.now(timezone.utc)), "count": 1}


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


# ── The list ─────────────────────────────────────────────────────────────────

@router.get("/tasks")
async def list_tasks(
    view: str = Query("default"),
    kind: Optional[Literal["call", "email", "todo"]] = Query(None),
    user: dict = Depends(get_current_user),
):
    reach = await _reach(user)
    role = reach["role"]
    try:
        rules = await tasks_service.load_rules()
        tz = zone(rules["time_zone"]) or timezone.utc
        now = datetime.now(timezone.utc)
        since = week_start(now, tz).isoformat()
        if role in MANAGERS:
            v = view if view in ("all", "mine", "unowned") else "all"
            open_rows, done_rows = await db.list_follow_ups(
                assignee_ids=[reach["id"]] if v == "mine" else None, only_unowned=v == "unowned", done_since=since)
        elif role in TEAM_LEADS:
            v = view if view in ("team", "mine", "unowned") else "team"
            open_rows, done_rows = await db.list_follow_ups(
                assignee_ids=[reach["id"]] if v == "mine" else reach["members"],
                with_unowned=v == "team", only_unowned=v == "unowned", done_since=since)
        else:
            v = "mine"
            open_rows, done_rows = await db.list_follow_ups(assignee_ids=[reach["id"]], done_since=since)
        unowned_open = None
        if _can_assign(reach):
            unowned_open = (sum(1 for t in open_rows if not t.get("assignee_id")) if v in ("all", "team", "unowned")
                            else len((await db.list_follow_ups(only_unowned=True))[0]))
        if kind:
            open_rows = [t for t in open_rows if t.get("kind") == kind]
            done_rows = [t for t in done_rows if t.get("kind") == kind]
        conv_ids = [t.get("conversation_id") for t in [*open_rows, *done_rows] if t.get("conversation_id")]
        convs, leads = await db.get_follow_up_context(conv_ids)
        made = [parse_ts(t.get("created_at")) for t in open_rows if t.get("conversation_id")]
        made = [m for m in made if m]
        client_msgs = await db.client_messages_since(
            [t["conversation_id"] for t in open_rows if t.get("conversation_id")], min(made).isoformat()) if made else []
        names = await db.get_user_names()
        people = await db.get_task_people() if _can_assign(reach) else []
    except db.TasksNotSetUp:
        raise HTTPException(503, NOT_SET_UP)
    except Exception as e:
        logger.error(f"[tasks] list failed: {e}", exc_info=True)
        raise HTTPException(502, "Couldn't load the tasks")

    data = build_tasks_view(open_rows=open_rows, done_rows=done_rows, convs=convs, leads=leads,
                            client_msgs=client_msgs, names=names, now=now, rules=rules, masked=reach["masked"])
    data.update({
        "view": v,
        "unowned_open": unowned_open,
        "has_team": reach["has_team"],
        "can": {"assign": _can_assign(reach), "rules": role in MANAGERS},
        # Who a task can be given to: the team (supervisor, PM) or everyone active (owner, admin, dev).
        "people": [{"id": p["id"], "name": p.get("name") or p.get("email") or "", "role": p.get("role")}
                   for p in people if reach["members"] is None or p.get("id") in reach["members"]],
    })
    return {"success": True, "data": data, "count": len(data["tasks"])}


# ── One task ─────────────────────────────────────────────────────────────────

async def _load_visible(task_id: str, reach: dict) -> dict:
    task = await db.get_follow_up(task_id)
    if not task or not _can_see(reach, task):
        raise HTTPException(404, "Task not found")
    return task


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, user: dict = Depends(get_current_user)):
    reach = await _reach(user)
    try:
        rules = await tasks_service.load_rules()
        task = await _load_visible(task_id, reach)
        cid = task.get("conversation_id")
        convs, leads = await db.get_follow_up_context([cid] if cid else [])
        conv, lead = convs.get(cid or ""), leads.get(cid or "")
        events = await db.get_task_events(task_id)
        around = await db.messages_around(cid, task.get("created_at")) if cid and task.get("created_at") else []
        names = await db.get_user_names()
    except HTTPException:
        raise
    except db.TasksNotSetUp:
        raise HTTPException(503, NOT_SET_UP)
    except Exception as e:
        logger.error(f"[tasks] task {task_id} read failed: {e}", exc_info=True)
        raise HTTPException(502, "Couldn't load the task")

    now = datetime.now(timezone.utc)
    tz = zone(rules["time_zone"]) or timezone.utc
    made = parse_ts(task.get("created_at"))
    later_client = [m for m in around if m.get("role") == "user" and made and (parse_ts(m.get("created_at")) or made) > made]
    row = task_row(task, conv=conv, lead=lead, names=names,
                   wrote_at=later_client[-1]["created_at"] if later_client else None, now=now, tz=tz, masked=reach["masked"])
    name = (conv or {}).get("visitor_name")
    first = name.strip().split()[0] if isinstance(name, str) and name.strip() else ""
    shown_first = client_label(name, masked=True) if reach["masked"] and name else first
    email, phone = (conv or {}).get("visitor_email"), (conv or {}).get("visitor_phone")
    history = history_of(events, names, client=shown_first, tz=tz)
    for m in later_client[-3:]:
        history.append({"at": m.get("created_at"), "kind": "client_wrote", "text": f"{shown_first or 'The client'} wrote again in the chat."})
    due = parse_ts(task.get("due_at"))
    if row["state"] == "late" and due:
        history.append({"at": due.isoformat(), "kind": "late", "text": "Late."})
    history.sort(key=lambda h: h.get("at") or "")
    row.update({
        "contact": {
            "email": mask_contacts(email) if reach["masked"] else email,
            "phone": mask_contacts(phone) if reach["masked"] else phone,
        },
        "crm": {"in_crm": bool((lead or {}).get("created_in_crm")), "crm_lead_id": (lead or {}).get("crm_lead_id"), "has_lead": bool(lead)},
        "excerpt": excerpt_of(around, task=task, client_first=shown_first,
                              agent_name=names.get(task.get("created_by") or "") if task.get("source") == "caught" else None,
                              masked=reach["masked"]),
        "history": history,
        "can": {
            "assign": _can_assign(reach),
            "edit": _can_assign(reach) or task.get("assignee_id") == reach["id"] or task.get("created_by") == reach["id"],
            "not_a_promise": task.get("source") == "caught",
            "delete": reach["role"] in MANAGERS,
        },
        "time_zone": rules["time_zone"],
    })
    return {"success": True, "data": row, "count": 1}


class TaskIn(BaseModel):
    kind: Literal["call", "email", "todo"] = "todo"
    title: str = Field(..., min_length=1, max_length=300)
    due_at: Optional[str] = None
    due_kind: Literal["picked", "day"] = "picked"
    assignee_id: Optional[str] = None          # None = me
    unowned: bool = False
    conversation_id: Optional[str] = None
    lead_id: Optional[str] = None
    source: Literal["added", "dashboard"] = "added"


async def _check_assignee(reach: dict, assignee_id: str) -> None:
    if assignee_id == reach["id"]:
        return
    if not _can_assign(reach):
        raise HTTPException(403, "You can only add tasks for yourself")
    if reach["members"] is not None and assignee_id not in reach["members"]:
        raise HTTPException(403, "You can give tasks only to your team")
    if not any(p.get("id") == assignee_id for p in await db.get_task_people()):
        raise HTTPException(400, "That person can't take tasks")


@router.post("/tasks")
async def create_task(body: TaskIn, user: dict = Depends(get_current_user)):
    reach = await _reach(user)
    if body.unowned and not _can_assign(reach):
        raise HTTPException(403, "Only a supervisor or manager leaves a task Unowned")
    assignee = None if body.unowned else (body.assignee_id or reach["id"])
    if assignee:
        await _check_assignee(reach, assignee)
    due = _iso(body.due_at)
    try:
        conversation_id, lead_id = body.conversation_id, body.lead_id
        if lead_id and not conversation_id:
            full = await db.get_lead_full(lead_id)
            conversation_id = (full or {}).get("conversation_id")
        task = await db.insert_follow_up({
            "title": body.title.strip(), "kind": body.kind, "source": body.source,
            "conversation_id": conversation_id, "lead_id": lead_id,
            "due_at": due, "due_kind": body.due_kind if due else "day",
            "assignee_id": assignee, "created_by": reach["id"],
            "unowned_reason": None, "unowned_at": None if assignee else datetime.now(timezone.utc).isoformat(),
        })
        if not task:
            raise HTTPException(500, "Couldn't add the task")
        await db.add_task_event(task["id"], body.source, reach["id"], {"to": assignee})
    except HTTPException:
        raise
    except db.TasksNotSetUp:
        raise HTTPException(503, NOT_SET_UP)
    except Exception as e:
        logger.error(f"[tasks] create failed: {e}", exc_info=True)
        raise HTTPException(502, "Couldn't add the task")
    return {"success": True, "data": task, "count": 1}


class TaskPatch(BaseModel):
    due_at: Optional[str] = None
    due_kind: Optional[Literal["picked", "day"]] = None
    assignee_id: Optional[str] = None
    unowned: Optional[bool] = None
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    kind: Optional[Literal["call", "email", "todo"]] = None
    done: Optional[bool] = None
    not_a_promise: Optional[bool] = None


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: TaskPatch, user: dict = Depends(get_current_user)):
    reach = await _reach(user)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "Nothing to change")
    try:
        task = await _load_visible(task_id, reach)
        mine = task.get("assignee_id") == reach["id"] or task.get("created_by") == reach["id"]
        if not (mine or _can_assign(reach)):
            raise HTTPException(403, "This task isn't yours")
        payload: dict = {}
        events: list[tuple[str, dict]] = []
        now = datetime.now(timezone.utc).isoformat()

        if "assignee_id" in fields or fields.get("unowned"):
            if not _can_assign(reach):
                raise HTTPException(403, "Only a supervisor or manager gives tasks to others")
            to = None if fields.get("unowned") else fields.get("assignee_id")
            if to:
                await _check_assignee(reach, to)
            payload.update({"assignee_id": to, "unowned_reason": None, "unowned_from": None if to else task.get("assignee_id"),
                            "unowned_at": None if to else now})
            events.append(("given", {"to": to}))
        if "due_at" in fields:
            payload.update({"due_at": _iso(fields["due_at"]), "due_kind": fields.get("due_kind", "picked")})
            events.append(("due_changed", {"due_at": payload["due_at"]}))
        if "title" in fields or "kind" in fields:
            payload.update({k: fields[k].strip() if k == "title" else fields[k] for k in ("title", "kind") if k in fields})
            events.append(("edited", {k: fields[k] for k in ("title", "kind") if k in fields}))
        if "done" in fields:
            payload.update({"done_at": now, "done_by": reach["id"]} if fields["done"] else {"done_at": None, "done_by": None})
            events.append(("done" if fields["done"] else "reopened", {}))
        if fields.get("not_a_promise"):
            if task.get("source") != "caught":
                raise HTTPException(400, "Only a task caught from a message can be “not a promise”")
            payload.update({"dismissed_at": now, "dismissed_by": reach["id"]})
            events.append(("dismissed", {}))
        if not payload:
            raise HTTPException(400, "Nothing to change")
        updated = await db.update_follow_up(task_id, payload)
        if not updated:
            raise HTTPException(404, "Task not found")
        for kind, details in events:
            await db.add_task_event(task_id, kind, reach["id"], details)
    except HTTPException:
        raise
    except db.TasksNotSetUp:
        raise HTTPException(503, NOT_SET_UP)
    except Exception as e:
        logger.error(f"[tasks] update {task_id} failed: {e}", exc_info=True)
        raise HTTPException(502, "Couldn't save the task")
    return {"success": True, "data": updated, "count": 1}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, user: dict = Depends(get_current_user)):
    if (user.get("role") or "") not in MANAGERS:
        raise HTTPException(403, "Only an owner, admin or developer deletes tasks")
    try:
        deleted = await db.delete_follow_up(task_id)
    except db.TasksNotSetUp:
        raise HTTPException(503, NOT_SET_UP)
    except Exception as e:
        logger.error(f"[tasks] delete {task_id} failed: {e}", exc_info=True)
        raise HTTPException(502, "Couldn't delete the task")
    if not deleted:
        raise HTTPException(404, "Task not found")
    return {"success": True, "data": None, "count": 0}
