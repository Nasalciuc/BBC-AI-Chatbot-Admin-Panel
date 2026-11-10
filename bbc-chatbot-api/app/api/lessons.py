"""Admin API — the human gate on the daily learning loop.

The loop only ever proposes. These two endpoints are what turns a proposal into
something the chatbot actually says. A proper approval screen (list + cards +
Approve/Retire) is the month-1 follow-up; curl is the interim.
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.security.auth import get_current_user
from app.services.learning import (
    get_lesson,
    get_lessons,
    invalidate_lesson_cache,
    set_lesson_status,
)

logger = logging.getLogger(__name__)
router = APIRouter()

CAN_REVIEW_LESSONS = {"owner", "admin", "dev"}
VALID_STATUSES = {"proposed", "approved", "retired"}


class LessonStatusUpdate(BaseModel):
    status: str
    # Required when approving a lesson the sanitizer flagged. Without it the
    # PATCH returns 422 — the second look is the whole point of needs_scrutiny.
    acknowledge_scrutiny: bool = False


def _assert_reviewer(user: dict) -> None:
    if user.get("role") not in CAN_REVIEW_LESSONS:
        raise HTTPException(403, "Only owner/admin can review chatbot lessons")


@router.get("/admin/lessons")
async def list_lessons(
    status: Optional[str] = Query(None, pattern="^(proposed|approved|retired)$"),
    user: dict = Depends(get_current_user),
):
    """Lessons with their evidence. Contradictions first — a pattern that argues
    with an approved lesson is the highest-value thing the loop can find.
    Responses include `needs_scrutiny` so curl/UI can see which ones need eyes."""
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
    """Approve a lesson (it enters the prompt within the cache TTL) or retire it.

    Approving a `needs_scrutiny` lesson requires
    `{"status":"approved","acknowledge_scrutiny":true}` — otherwise 422.
    """
    _assert_reviewer(user)
    if body.status not in VALID_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(VALID_STATUSES)}")

    if body.status == "approved":
        current = await get_lesson(lesson_id)
        if not current:
            raise HTTPException(404, "Lesson not found")
        if current.get("needs_scrutiny") and not body.acknowledge_scrutiny:
            raise HTTPException(
                422,
                "Lesson is flagged needs_scrutiny — re-read the content, then "
                "PATCH with {\"status\":\"approved\",\"acknowledge_scrutiny\":true}",
            )

    updated = await set_lesson_status(lesson_id, body.status)
    if not updated:
        raise HTTPException(404, "Lesson not found")

    invalidate_lesson_cache()
    logger.info(f"Lesson {lesson_id} → {body.status} by user={user.get('id')}")
    return updated


# ── Question variants + learning report (PR-8) ────────────────────────────
VALID_VARIANT_STATUSES = {"proposed", "active", "retired"}


class VariantStatusUpdate(BaseModel):
    status: str


@router.get("/admin/question-variants")
async def list_question_variants(
    slot: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    """Question variants per slot with their outcome stats (shows, answered,
    rate, P(best))."""
    _assert_reviewer(user)
    from app.services.learning import get_question_variants

    try:
        data = await get_question_variants(slot)
    except Exception as e:
        logger.warning(f"[variants] list failed: {type(e).__name__}: {e}")
        raise HTTPException(502, "Could not load question variants") from e
    return {"success": True, "data": data, "count": len(data)}


@router.patch("/admin/question-variants/{variant_id}")
async def update_question_variant(
    variant_id: str,
    body: VariantStatusUpdate,
    user: dict = Depends(get_current_user),
):
    """Approve (→active), retire or re-propose a variant."""
    _assert_reviewer(user)
    if body.status not in VALID_VARIANT_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(VALID_VARIANT_STATUSES)}")
    try:
        uuid.UUID(variant_id)
    except ValueError:
        raise HTTPException(404, "Variant not found") from None  # a malformed id names no variant
    from app.services.learning import set_question_variant_status

    try:
        ok = await set_question_variant_status(variant_id, body.status)
    except Exception as e:
        # The update failed; the variant may well exist (CLAUDE.md rule 7).
        logger.warning(f"[variants] update failed: {type(e).__name__}: {e}")
        raise HTTPException(502, "Could not update the variant") from e
    if not ok:
        raise HTTPException(404, "Variant not found")
    return {"success": True, "data": {"id": variant_id, "status": body.status}, "count": 1}


@router.get("/admin/learning/report")
async def learning_report(user: dict = Depends(get_current_user)):
    """Treatment-vs-control complete-detail rate and per-variant answer rates."""
    _assert_reviewer(user)
    from app.services.learning import build_learning_report

    report = await build_learning_report()
    if report.get("error"):
        return {"success": False, "data": report, "count": 0, "error": report["error"]}
    return {"success": True, "data": report, "count": 1}
