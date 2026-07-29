"""Admin API — the human gate on the daily learning loop.

The loop only ever proposes. These two endpoints are what turns a proposal into
something the chatbot actually says. A proper approval screen (list + cards +
Approve/Retire) is the month-1 follow-up; curl is the interim.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.security.auth import get_current_user
from app.services.learning import get_lessons, invalidate_lesson_cache, set_lesson_status

logger = logging.getLogger(__name__)
router = APIRouter()

CAN_REVIEW_LESSONS = {"owner", "admin", "dev"}
VALID_STATUSES = {"proposed", "approved", "retired"}


class LessonStatusUpdate(BaseModel):
    status: str


def _assert_reviewer(user: dict) -> None:
    if user.get("role") not in CAN_REVIEW_LESSONS:
        raise HTTPException(403, "Only owner/admin can review chatbot lessons")


@router.get("/admin/lessons")
async def list_lessons(
    status: Optional[str] = Query(None, pattern="^(proposed|approved|retired)$"),
    user: dict = Depends(get_current_user),
):
    """Lessons with their evidence. Contradictions first — a pattern that argues
    with an approved lesson is the highest-value thing the loop can find."""
    _assert_reviewer(user)
    lessons = await get_lessons(status)
    lessons.sort(key=lambda x: (x.get("contradicts_lesson_id") is None, -(x.get("evidence_count") or 0)))
    return {"lessons": lessons, "count": len(lessons)}


@router.patch("/admin/lessons/{lesson_id}")
async def update_lesson_status(
    lesson_id: str,
    body: LessonStatusUpdate,
    user: dict = Depends(get_current_user),
):
    """Approve a lesson (it enters the prompt within the cache TTL) or retire it."""
    _assert_reviewer(user)
    if body.status not in VALID_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(VALID_STATUSES)}")

    updated = await set_lesson_status(lesson_id, body.status)
    if not updated:
        raise HTTPException(404, "Lesson not found")

    invalidate_lesson_cache()
    logger.info(f"Lesson {lesson_id} → {body.status} by {user.get('email') or user.get('id')}")
    return updated
