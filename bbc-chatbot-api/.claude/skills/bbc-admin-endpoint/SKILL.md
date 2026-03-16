---
name: bbc-admin-endpoint
description: Pattern for adding new admin CRUD endpoints to the BBC Chatbot API — route, service, model, tests
applyTo: "app/**,tests/**"
---

# BBC Admin Endpoint Skill

## Purpose

Guide Claude through adding a new admin CRUD endpoint following the established Route → Service → DB pattern with Pydantic models and the standard response envelope.

## Endpoint Scaffold

When adding a new admin endpoint (e.g., `/api/admin/widgets`), create these files:

### 1. Pydantic Model (`app/models/widget.py`)

```python
from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime


class Widget(BaseModel):
    id: UUID
    name: str
    status: str = "active"
    created_at: datetime
    updated_at: Optional[datetime] = None


class WidgetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    status: str = Field(default="active", pattern="^(active|inactive|archived)$")


class WidgetUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    status: Optional[str] = Field(None, pattern="^(active|inactive|archived)$")
```

### 2. Service (`app/services/widget_service.py`)

```python
import logging
from typing import Optional
from app.db.supabase_client import get_supabase

logger = logging.getLogger(__name__)


async def get_widgets(page: int = 1, limit: int = 20, status: Optional[str] = None):
    supabase = get_supabase()
    query = supabase.table("widgets").select("*", count="exact")

    if status:
        query = query.eq("status", status)

    offset = (page - 1) * limit
    query = query.range(offset, offset + limit - 1).order("created_at", desc=True)

    result = query.execute()
    return result.data, result.count


async def get_widget_by_id(widget_id: str):
    supabase = get_supabase()
    result = supabase.table("widgets").select("*").eq("id", widget_id).single().execute()
    return result.data


async def create_widget(data: dict):
    supabase = get_supabase()
    result = supabase.table("widgets").insert(data).execute()
    return result.data[0] if result.data else None


async def update_widget(widget_id: str, data: dict):
    supabase = get_supabase()
    result = supabase.table("widgets").update(data).eq("id", widget_id).execute()
    return result.data[0] if result.data else None
```

### 3. Route (`app/api/widgets.py`)

```python
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.security.auth import verify_credentials
from app.services import widget_service
from app.models.widget import WidgetCreate, WidgetUpdate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/widgets", tags=["widgets"])


@router.get("")
async def get_widgets(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    _=Depends(verify_credentials),
):
    try:
        data, count = await widget_service.get_widgets(page=page, limit=limit, status=status)
        return {"success": True, "data": data, "count": count, "error": None}
    except Exception as e:
        logger.error(f"Failed to fetch widgets: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch widgets")


@router.get("/{widget_id}")
async def get_widget(widget_id: str, _=Depends(verify_credentials)):
    try:
        data = await widget_service.get_widget_by_id(widget_id)
        if not data:
            raise HTTPException(status_code=404, detail="Widget not found")
        return {"success": True, "data": data, "count": 1, "error": None}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch widget {widget_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch widget")


@router.post("")
async def create_widget(body: WidgetCreate, _=Depends(verify_credentials)):
    try:
        data = await widget_service.create_widget(body.model_dump())
        return {"success": True, "data": data, "count": 1, "error": None}
    except Exception as e:
        logger.error(f"Failed to create widget: {e}")
        raise HTTPException(status_code=500, detail="Failed to create widget")


@router.patch("/{widget_id}")
async def update_widget(widget_id: str, body: WidgetUpdate, _=Depends(verify_credentials)):
    try:
        update_data = body.model_dump(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")
        data = await widget_service.update_widget(widget_id, update_data)
        if not data:
            raise HTTPException(status_code=404, detail="Widget not found")
        return {"success": True, "data": data, "count": 1, "error": None}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update widget {widget_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update widget")
```

### 4. Register in `app/main.py`

```python
from app.api.widgets import router as widgets_router
app.include_router(widgets_router)
```

### 5. Test (`tests/test_widgets.py`)

```python
import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_get_widgets():
    mock_data = [{"id": "uuid-1", "name": "Test", "status": "active"}]
    with patch("app.services.widget_service.get_widgets", new_callable=AsyncMock) as mock:
        mock.return_value = (mock_data, 1)
        # Call route handler or use TestClient
        data, count = await mock()
        assert count == 1
        assert data[0]["name"] == "Test"
```

## Response Envelope

ALL admin endpoints MUST return:

```json
{
  "success": true,
  "data": "<T | T[]>",
  "count": 1,
  "error": null
}
```

On error:

```json
{
  "success": false,
  "data": null,
  "count": 0,
  "error": "Human-readable error message"
}
```

## Checklist for New Endpoint

1. [ ] Pydantic model in `app/models/`
2. [ ] Service in `app/services/` (no direct DB in routes)
3. [ ] Route in `app/api/` with `verify_credentials` dependency
4. [ ] Router registered in `app/main.py`
5. [ ] Response uses `{ success, data, count, error }` envelope
6. [ ] Query params validated with `Query(default, ge=, le=)`
7. [ ] Errors logged, not leaked to client
8. [ ] Test in `tests/`
9. [ ] Endpoint documented in `specs/api-contract.md`
10. [ ] Frontend types updated in `bbc-admin-app/src/lib/types.ts`

## Do NOT

- Access Supabase directly from route handlers — go through services.
- Return raw exceptions to the client.
- Skip `verify_credentials` on any admin endpoint.
- Use positional args for query parameters — always use `Query()`.
- Forget to update `specs/api-contract.md` with the new endpoint.
