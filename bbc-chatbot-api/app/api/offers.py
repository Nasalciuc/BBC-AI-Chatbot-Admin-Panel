"""Offers to send (ADR-6b, ADR-8b): the queue the Tasks page opens on, and the mark a consultant leaves
once the written offer is out (or isn't needed). The rows: app.services.offers_view.

Who sees it, as the Leads page and the chats decide:
  - owner, admin, dev and QA: every lead; QA reads, never marks;
  - a supervisor: their team's leads, contacts masked; none without a team;
  - an agent: their tunnel's leads; a chat still waiting for a person stays blind (who and since when,
    never the trip or the contact) until they take it — the chats' rule, read fresh for the queue's
    chats (the universe is cached for 20 s); a failed read hides every row's details;
  - support and project managers: no leads, no queue (403).
A failed read is a 502, never an empty queue that reads as "nothing to send".
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.conversations import _blind_guard, _enforce_tunnel, _taken_blind
from app.db import supabase as db
from app.security.auth import get_current_user
from app.services.offers_view import DEFAULT_DAYS, MARK_KEYS, WINDOWS, build_offers, candidates, mark_patch

logger = logging.getLogger(__name__)
router = APIRouter()

READERS = {"owner", "admin", "dev", "qa", "sales", "supervisor"}
MARKERS = {"owner", "admin", "dev", "sales", "supervisor"}
OFFERS_HEALTH: dict = {"marks": 0, "mark_failed": 0, "blind_read_failed": 0, "rollback_failed": 0}
# One mark covers every chat of the client the queue shows (CodeRabbit #270: a client with 22 chats could
# not be marked under a limit of 49 sent by a panel that cut the list). The bound is only against abuse.
MAX_CHATS = 500


def _blind_inputs(row: dict) -> dict:
    """A universe row in the shape the chats' blind rule reads."""
    return {
        "status": row.get("conversation_status"),
        "mode": row.get("conversation_mode"),
        "assigned_agent_id": row.get("assigned_agent_id"),
        "queued_at": row.get("queued_at"),
        "engaged_agent_id": row.get("engaged_agent_id"),
    }


@router.get("/offers")
async def list_offers(
    days: int = Query(DEFAULT_DAYS, description="Leads from the last 1, 3, 7 or 14 days."),
    user: dict = Depends(get_current_user),
):
    role = user.get("role") or ""
    if role not in READERS:
        raise HTTPException(403, "The offers queue is for the sales team")
    if days not in WINDOWS:
        raise HTTPException(400, "days must be 1, 3, 7 or 14")
    try:
        rows = await db.get_leads_universe()
        names = await db.get_user_names()
    except Exception as e:
        logger.error(f"[offers] read failed: {type(e).__name__}: {e}")
        raise HTTPException(502, "Couldn't load the offers") from e
    now = datetime.now(timezone.utc)
    fresh: Optional[dict] = {}
    if role == "supervisor":
        team_ids = set(await db.get_team_ids_for_supervisor(user.get("id", "")) or [])
        rows = [r for r in rows if r.get("team_id") in team_ids] if team_ids else []
    elif role in db._OPERATOR_ROLES:
        scope = _enforce_tunnel(user, None)
        if scope and scope != "all":
            rows = [r for r in rows if (r.get("tunnel") or "sales") == scope]
        # The blind rule on the chats' state now: the universe can be 20 s old, and a chat that entered the
        # shared line since is blind already (CodeRabbit #270). Not readable: every row stays blind.
        try:
            fresh = await db.get_blind_inputs_many(
                [r.get("conversation_id") for r in candidates(rows, now=now, days=days)]
            )
        except Exception as e:
            OFFERS_HEALTH["blind_read_failed"] += 1
            logger.error(f"[offers] blind state read failed ({type(e).__name__}): every row blind")
            fresh = None

    def blind(row: dict) -> bool:
        if role not in db._OPERATOR_ROLES:
            return False
        state = fresh.get(row.get("conversation_id")) if fresh is not None else None
        return True if state is None else _taken_blind(user, state)

    built = build_offers(rows, now=now, days=days, names=names, masked=role == "supervisor", blind=blind)
    return {
        "success": True,
        "data": {**built, "days": days, "can": {"mark": role in MARKERS}},
        "count": len(built["waiting"]),
    }


class OfferMark(BaseModel):
    state: Literal["sent", "not_needed", "open"]
    # The other chats of the same client (the row's `chats`): one mark for one client.
    chats: list[str] = Field(default_factory=list, max_length=MAX_CHATS)


@router.post("/offers/{conversation_id}")
async def mark_offer(conversation_id: str, body: OfferMark, user: dict = Depends(get_current_user)):
    """Sent, not needed, or open again (Undo). Saved on each chat's metadata without touching updated_at,
    so a mark never moves a chat in the lists."""
    role = user.get("role") or ""
    if role not in MARKERS:
        raise HTTPException(403, "Only the sales team marks offers")
    ids = list(dict.fromkeys([conversation_id, *body.chats]))
    for cid in ids:
        try:
            uuid.UUID(cid)
        except ValueError:
            raise HTTPException(400, "Unknown chat")
    team_ids = set(await db.get_team_ids_for_supervisor(user.get("id", "")) or []) if role == "supervisor" else None
    before: dict[str, dict] = {}
    for cid in ids:
        conv = await db.get_conversation_simple(cid)
        if not conv:
            raise HTTPException(404, "Chat not found")
        _enforce_tunnel(user, conv.get("tunnel"))
        if team_ids is not None and conv.get("team_id") not in team_ids:
            raise HTTPException(403, "Outside your team")
        if await _blind_guard(user, cid):
            raise HTTPException(403, "Take the chat first: it's waiting for a person.")
        meta = conv.get("metadata") if isinstance(conv.get("metadata"), dict) else {}
        before[cid] = {k: meta.get(k) for k in MARK_KEYS}
    now = datetime.now(timezone.utc)
    patch = mark_patch(body.state, user.get("id") or "", now)
    done: list[str] = []
    try:
        for cid in ids:
            if not await db.patch_conversation_presence(cid, patch):
                OFFERS_HEALTH["mark_failed"] += 1
                # One client, one mark: the chats already marked go back to what they were, so the client
                # never stands half in the queue and half out of it (CodeRabbit #270).
                for back in done:
                    if not await db.patch_conversation_presence(back, before[back]):
                        OFFERS_HEALTH["rollback_failed"] += 1
                        logger.error(f"[offers] mark undo failed on one chat of {len(ids)}")
                raise HTTPException(503, "Couldn't save the mark. Try again.")
            done.append(cid)
    finally:
        if done:
            db.invalidate_leads_universe()
    OFFERS_HEALTH["marks"] += 1
    logger.info(f"[offers] {body.state} role={role} chats={len(ids)}")
    return {"success": True, "data": {"state": body.state, "at": now.isoformat(), "chats": ids}, "count": len(ids)}
