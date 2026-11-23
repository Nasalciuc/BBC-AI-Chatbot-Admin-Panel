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
from app.services.offers_view import (
    DEFAULT_DAYS, MARK_KEYS, WINDOWS, build_offers, candidates, mark_patch, owner_of,
)

logger = logging.getLogger(__name__)
router = APIRouter()

READERS = {"owner", "admin", "dev", "qa", "sales", "supervisor"}
MARKERS = {"owner", "admin", "dev", "sales", "supervisor"}
OFFERS_HEALTH: dict = {
    "marks": 0, "mark_failed": 0, "blind_read_failed": 0, "rollback_failed": 0, "rollback_skipped": 0,
}
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

    def owner_now(row: dict) -> tuple[bool, Optional[str]]:
        """(known, whose the lead is NOW) from the fresh read: the universe can be 20 s old, and a lead given,
        moved or taken back since must be shown as it is now. Unknown when the fresh read can't speak for it."""
        state = fresh.get(row.get("conversation_id")) if fresh is not None else None
        if state is None:
            return False, None
        # Who closed it names the owner only while it is still closed: a chat reopened since is the agent who
        # took it now, never the earlier closer (CodeRabbit #273). All three read fresh.
        closed_now = state.get("status") in ("closed", "completed")
        return True, (state.get("assigned_agent_id") or (state.get("closed_by_agent_id") if closed_now else None)
                      or state.get("engaged_agent_id"))

    def shown_owner(row: dict) -> Optional[str]:
        """The agent the row names: the same reading as the visibility rules when the fresh read speaks for the
        lead, the universe's otherwise (a role that reads no fresh state, or a read that failed)."""
        known, owner = owner_now(row)
        return owner if known else owner_of(row)

    def blind(row: dict) -> bool:
        if role not in db._OPERATOR_ROLES:
            return False
        known, _owner = owner_now(row)
        if not known:
            return True
        return _taken_blind(user, fresh[row.get("conversation_id")])

    built = build_offers(rows, now=now, days=days, names=names, masked=role == "supervisor", blind=blind,
                         owner=shown_owner)
    return {
        "success": True,
        "data": {**built, "days": days, "can": {"mark": role in MARKERS}},
        "count": len(built["waiting"]),
    }


async def _put_back(cid: str, ours: dict, before: dict, of: int) -> None:
    """Undo this request's mark on one chat — only while the mark there is still this request's own
    (its time and its author). A mark another request saved since stays: an undo never writes an older
    state over it (CodeRabbit #270). A chat that can't be read keeps this mark; the 503 asks for a retry."""
    try:
        conv = await db.get_conversation_simple(cid)
    except Exception as e:  # noqa: silent — counted and logged; the mark stays, the agent retries
        conv = None
        logger.warning(f"[offers] mark undo: chat not read ({type(e).__name__})")
    meta = (conv or {}).get("metadata") if isinstance((conv or {}).get("metadata"), dict) else None
    if meta is not None and all(meta.get(k) == before.get(k) for k in MARK_KEYS):
        return  # nothing of this request's landed there: nothing to undo
    if meta is None or any(meta.get(k) != ours.get(k) for k in MARK_KEYS):
        OFFERS_HEALTH["rollback_skipped"] += 1
        return
    if not await db.patch_conversation_presence(cid, before):
        OFFERS_HEALTH["rollback_failed"] += 1
        logger.error(f"[offers] mark undo failed on one chat of {of}")


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
    tried = False
    try:
        for cid in ids:
            tried = True
            if not await db.patch_conversation_presence(cid, patch):
                OFFERS_HEALTH["mark_failed"] += 1
                # One client, one mark: the chats already marked go back to what they were, so the client
                # never stands half in the queue and half out of it (CodeRabbit #270). The chat whose save
                # failed goes back too: a timeout can come after the write landed. _put_back leaves a chat
                # alone when the mark there is not this request's.
                for back in [*done, cid]:
                    await _put_back(back, patch, before[back], len(ids))
                raise HTTPException(503, "Couldn't save the mark. Try again.")
            done.append(cid)
    finally:
        # Any write tried may have landed: the queue is read again either way.
        if tried:
            db.invalidate_leads_universe()
    OFFERS_HEALTH["marks"] += 1
    logger.info(f"[offers] {body.state} role={role} chats={len(ids)}")
    return {"success": True, "data": {"state": body.state, "at": now.isoformat(), "chats": ids}, "count": len(ids)}
