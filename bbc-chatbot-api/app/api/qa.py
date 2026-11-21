"""The QA page's API: the day's sample, a verdict on each chat, and the week per agent (app.services.qa_sample).

QA, owner, admin and dev only. Agents never reach it, and nothing they can read says a chat of theirs was
picked (the sample lives in audit_log). A failed read is a 502, never an empty sample that reads as
"nothing to check".
"""

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.security.auth import get_current_user
from app.services import qa_sample

logger = logging.getLogger(__name__)
router = APIRouter()

READERS = {"qa", "owner", "admin", "dev"}


def _gate(user: dict) -> None:
    if (user.get("role") or "") not in READERS:
        raise HTTPException(403, "The QA sample is for QA and management")


def _day(raw: Optional[str]) -> str:
    if not raw:
        return (datetime.now(timezone.utc).astimezone(qa_sample._ny()).date() - timedelta(days=1)).isoformat()
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as e:
        raise HTTPException(400, "day must be YYYY-MM-DD") from e


@router.get("/qa/sample")
async def get_sample(day: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    _gate(user)
    d = _day(day)
    try:
        data = await qa_sample.sample_of(d)
    except Exception as e:
        logger.error(f"[qa] sample of {d} not read: {type(e).__name__}: {e}")
        raise HTTPException(502, "Couldn't load the sample") from e
    return {"success": True, "data": data, "count": len(data["items"])}


class Verdict(BaseModel):
    day: str
    verdict: Literal["ok", "problem", "discuss"]
    note: Optional[str] = Field(None, max_length=1000)


@router.patch("/qa/sample/{conversation_id}")
async def set_verdict(conversation_id: str, body: Verdict, user: dict = Depends(get_current_user)):
    _gate(user)
    try:
        uuid.UUID(conversation_id)
    except ValueError as e:
        raise HTTPException(400, "Unknown chat") from e
    d = _day(body.day)
    try:
        await qa_sample.give_verdict(d, conversation_id, body.verdict, body.note, user)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        logger.error(f"[qa] verdict on {conversation_id} not saved: {type(e).__name__}: {e}")
        raise HTTPException(503, "Couldn't save the verdict. Try again.") from e
    return {"success": True, "data": {"conversation_id": conversation_id, "verdict": body.verdict}, "count": 1}


@router.get("/qa/stats")
async def get_stats(days: int = Query(7, ge=1, le=90), user: dict = Depends(get_current_user)):
    _gate(user)
    try:
        rows = await qa_sample.stats(days)
    except Exception as e:
        logger.error(f"[qa] stats not read: {type(e).__name__}: {e}")
        raise HTTPException(502, "Couldn't load the stats") from e
    return {"success": True, "data": rows, "count": len(rows)}
