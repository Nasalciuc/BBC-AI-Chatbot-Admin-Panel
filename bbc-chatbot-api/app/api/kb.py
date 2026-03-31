"""Admin API — Knowledge Base CRUD."""
import logging
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from app.db import supabase as db
from app.db.qdrant import upsert_kb_entry_dict, delete_kb_entry_qdrant, sync_kb_entries
from app.models.admin import KBEntryCreate, KBEntryUpdate

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/kb/categories")
async def list_categories(tunnel: Optional[str] = Query(None, pattern="^(sales|support)$")):
    try:
        rows = await db.get_kb_categories(tunnel=tunnel)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        return {"success": False, "data": [], "count": 0, "error": str(e)}


@router.get("/kb/entries")
async def list_entries(
    tunnel:      Optional[str]  = Query(None),
    category_id: Optional[str]  = Query(None),
    is_active:   Optional[bool] = Query(None),
    limit:       int            = Query(100, ge=1, le=500),
):
    try:
        rows = await db.get_kb_entries(tunnel=tunnel, category_id=category_id, is_active=is_active, limit=limit)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        return {"success": False, "data": [], "count": 0, "error": str(e)}


@router.post("/kb/entries", status_code=201)
async def create_entry(body: KBEntryCreate, background_tasks: BackgroundTasks):
    try:
        result = await db.create_kb_entry(body.model_dump())
        if not result:
            return {"success": False, "data": None, "count": 0, "error": "Failed to create KB entry"}
        background_tasks.add_task(upsert_kb_entry_dict, result)
        return {"success": True, "data": result, "count": 1}
    except Exception as e:
        return {"success": False, "data": None, "count": 0, "error": str(e)}


@router.patch("/kb/entries/{entry_id}")
async def update_entry(entry_id: str, body: KBEntryUpdate, background_tasks: BackgroundTasks):
    payload = body.model_dump(exclude_none=True)
    if not payload:
        return {"success": False, "data": None, "count": 0, "error": "No fields to update"}
    try:
        result = await db.update_kb_entry(entry_id, payload)
        if not result:
            return {"success": False, "data": None, "count": 0, "error": "KB entry not found"}
        background_tasks.add_task(upsert_kb_entry_dict, result)
        return {"success": True, "data": result, "count": 1}
    except Exception as e:
        return {"success": False, "data": None, "count": 0, "error": str(e)}


@router.delete("/kb/entries/{entry_id}", status_code=204)
async def delete_entry(entry_id: str, background_tasks: BackgroundTasks):
    if not await db.delete_kb_entry(entry_id):
        raise HTTPException(404, "KB entry not found")
    background_tasks.add_task(delete_kb_entry_qdrant, entry_id)


@router.post("/kb/sync")
async def sync_kb(background_tasks: BackgroundTasks):
    """Resync all KB entries from Supabase to Qdrant. Use after bulk changes."""
    try:
        entries = await db.get_kb_entries(is_active=True, limit=500)
        if not entries:
            return {"success": True, "data": {"synced": 0}, "count": 0}
        background_tasks.add_task(sync_kb_entries, entries)
        return {"success": True, "data": {"queued": len(entries)}, "count": len(entries)}
    except Exception as e:
        logger.error(f"sync_kb error: {e}")
        return {"success": False, "data": None, "count": 0, "error": str(e)}
