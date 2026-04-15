"""Admin API — tasks CRUD."""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import supabase as db

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_STATUSES = {"backlog", "todo", "in progress", "done", "canceled"}
VALID_LABELS = {"bug", "feature", "documentation"}
VALID_PRIORITIES = {"low", "medium", "high", "critical"}


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    status: str = "todo"
    label: str = "feature"
    priority: str = "medium"
    assignee_id: Optional[str] = None
    due_date: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = None
    status: Optional[str] = None
    label: Optional[str] = None
    priority: Optional[str] = None
    assignee_id: Optional[str] = None
    due_date: Optional[str] = None


@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    assignee_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    try:
        rows, total = await db.get_tasks(
            status=status, priority=priority,
            assignee_id=assignee_id, limit=limit, offset=offset,
        )
        # Resolve assignee names
        user_ids = {r["assignee_id"] for r in rows if r.get("assignee_id")}
        name_map: dict[str, str] = {}
        if user_ids:
            all_users, _ = await db.get_users(limit=200)
            name_map = {u["id"]: u.get("name", u.get("email", "")) for u in all_users}
        for row in rows:
            row["assignee_name"] = name_map.get(row.get("assignee_id", ""))
        return {"success": True, "data": rows, "count": total}
    except Exception as e:
        logger.error(f"list_tasks error: {e}")
        raise HTTPException(500, "Failed to load tasks")


@router.post("/tasks")
async def create_task(body: TaskCreate):
    if body.status not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {VALID_STATUSES}")
    if body.label not in VALID_LABELS:
        raise HTTPException(400, f"Invalid label. Must be one of: {VALID_LABELS}")
    if body.priority not in VALID_PRIORITIES:
        raise HTTPException(400, f"Invalid priority. Must be one of: {VALID_PRIORITIES}")

    payload = body.model_dump(exclude_none=True)
    task = await db.create_task(payload)
    if not task:
        raise HTTPException(500, "Failed to create task")
    return {"success": True, "data": task}


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: TaskUpdate):
    payload = body.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(400, "No fields to update")
    if "status" in payload and payload["status"] not in VALID_STATUSES:
        raise HTTPException(400, f"Invalid status. Must be one of: {VALID_STATUSES}")
    if "label" in payload and payload["label"] not in VALID_LABELS:
        raise HTTPException(400, f"Invalid label. Must be one of: {VALID_LABELS}")
    if "priority" in payload and payload["priority"] not in VALID_PRIORITIES:
        raise HTTPException(400, f"Invalid priority. Must be one of: {VALID_PRIORITIES}")

    task = await db.update_task(task_id, payload)
    if not task:
        raise HTTPException(404, "Task not found")
    return {"success": True, "data": task}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    deleted = await db.delete_task(task_id)
    if not deleted:
        raise HTTPException(404, "Task not found")
    return {"success": True}
