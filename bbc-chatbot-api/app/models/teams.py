"""Pydantic models for the Teams admin API (Phase 1)."""
from datetime import datetime, time
from typing import Optional

from pydantic import BaseModel, Field


class TeamCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    shift_name: Optional[str] = Field(None, max_length=100)
    shift_start: Optional[time] = None
    shift_end: Optional[time] = None
    supervisor_id: Optional[str] = None
    pm_id: Optional[str] = None


class TeamUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    shift_name: Optional[str] = Field(None, max_length=100)
    shift_start: Optional[time] = None
    shift_end: Optional[time] = None
    supervisor_id: Optional[str] = None
    pm_id: Optional[str] = None
    is_active: Optional[bool] = None


class TeamOut(BaseModel):
    id: str
    name: str
    shift_name: Optional[str] = None
    shift_start: Optional[str] = None
    shift_end: Optional[str] = None
    supervisor_id: Optional[str] = None
    pm_id: Optional[str] = None
    is_active: bool = True
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
