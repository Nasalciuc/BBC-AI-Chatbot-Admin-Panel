"""Admin API — Knowledge Base CRUD."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from app.db import supabase as db
from app.models.admin import KBEntryCreate, KBEntryUpdate

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
async def create_entry(body: KBEntryCreate):
    try:
        result = await db.create_kb_entry(body.model_dump())
        if not result:
            return {"success": False, "data": None, "count": 0, "error": "Failed to create KB entry"}
        return {"success": True, "data": result, "count": 1}
    except Exception as e:
        return {"success": False, "data": None, "count": 0, "error": str(e)}


@router.patch("/kb/entries/{entry_id}")
async def update_entry(entry_id: str, body: KBEntryUpdate):
    payload = body.model_dump(exclude_none=True)
    if not payload:
        return {"success": False, "data": None, "count": 0, "error": "No fields to update"}
    try:
        result = await db.update_kb_entry(entry_id, payload)
        if not result:
            return {"success": False, "data": None, "count": 0, "error": "KB entry not found"}
        return {"success": True, "data": result, "count": 1}
    except Exception as e:
        return {"success": False, "data": None, "count": 0, "error": str(e)}


@router.delete("/kb/entries/{entry_id}", status_code=204)
async def delete_entry(entry_id: str):
    if not await db.delete_kb_entry(entry_id):
        raise HTTPException(404, "KB entry not found")
