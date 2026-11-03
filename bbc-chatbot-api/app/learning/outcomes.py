"""The reward loop (PR-8): which question phrasing actually got the detail.

When an AI reply asks exactly one slot, record_ask() writes a question_outcomes
row with answered=NULL. On the client's next message, after extraction,
resolve_outcomes() sets answered=true if that slot is now filled on the lead,
else false. That true/false is what feeds the Thompson bandit.

A conversation is in the control group (bucket < CONTROL_GROUP_PERCENT, R11):
it always gets the seed variant and no lessons, and its outcomes are marked
control=true so the report can compare treatment vs control.

All writes are best-effort and gated by LEARNING_ENABLED; a missing table or a
failed write never disturbs a chat turn.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.experiments import bucket
from app.flags import flag

logger = logging.getLogger(__name__)

_SLOT_LEAD_FIELD = {
    "route": ("origin_code", "destination_code"),
    "departure_date": ("departure_date",),
    "return_date": ("return_date",),
    "passengers": ("passengers",),
    "trip_type": ("trip_type",),
}


def is_control(conversation_id: Optional[str]) -> bool:
    b = bucket("learning_control", conversation_id)
    if b is None:
        return False
    return b < int(flag("CONTROL_GROUP_PERCENT", 10))


def _slot_filled(slot: str, lead: dict) -> bool:
    fields = _SLOT_LEAD_FIELD.get(slot, ())
    return bool(fields) and all(lead.get(f) for f in fields)


async def record_ask(conversation_id: str, message_id: Optional[str], slot: str,
                     variant_id: Optional[str], segment: Optional[str] = None) -> None:
    if not flag("LEARNING_ENABLED", True) or not slot:
        return
    try:
        from app.db import supabase as db
        row = {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "slot": slot,
            "variant_id": variant_id,
            "segment": segment,
            "control": is_control(conversation_id),
            "answered": None,
        }
        await db._run_sync(
            lambda: db.get_client().table("question_outcomes").insert(row).execute(),
            idempotent=False, label="question_outcomes",
        )
    except Exception as e:
        logger.debug(f"[outcomes] record_ask skipped: {type(e).__name__}: {e}")


async def resolve_outcomes(conversation_id: str, lead: Optional[dict] = None) -> int:
    """Resolve this conversation's open asks against the current lead. Returns
    how many were resolved."""
    if not flag("LEARNING_ENABLED", True):
        return 0
    try:
        from app.db import supabase as db
        client = db.get_client()
        if lead is None:
            from app.services import lead_service
            lead = await lead_service.get_or_create_lead(conversation_id)
        res = await db._run_sync(
            lambda: client.table("question_outcomes").select("id,slot")
            .eq("conversation_id", conversation_id).is_("answered", "null").execute(),
            label="question_outcomes",
        )
        rows = getattr(res, "data", None) or []
        resolved = 0
        for r in rows:
            answered = _slot_filled(r["slot"], lead or {})
            await db._run_sync(
                lambda rid=r["id"], a=answered: client.table("question_outcomes")
                .update({"answered": a}).eq("id", rid).execute(),
                idempotent=False, label="question_outcomes",
            )
            resolved += 1
        return resolved
    except Exception as e:
        logger.debug(f"[outcomes] resolve skipped: {type(e).__name__}: {e}")
        return 0
