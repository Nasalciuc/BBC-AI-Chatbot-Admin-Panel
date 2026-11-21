"""A closed lead given by hand (managers), next to the follow-up lottery (app/services/lead_lottery.py).

POST /api/followups/{conversation_id}/assign {agent_id}
  - owner, admin, dev and supervisor; a supervisor gives only within their team;
  - closed and completed chats only: an open chat is transferred (reassign), and that transfer refuses
    closed ones, which is why this exists;
  - no message is written in the chat; the lead shows as "given by <manager>" and the agent's badge
    counts it until they open it. It never goes back on its own (no 15-minute confirmation).
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import supabase as db
from app.security.auth import get_current_user
from app.services import lead_lottery

logger = logging.getLogger(__name__)
router = APIRouter()

GIVERS = {"owner", "admin", "dev", "supervisor"}


class GiveBody(BaseModel):
    agent_id: str


@router.post("/followups/{conversation_id}/assign")
async def give_lead(conversation_id: str, body: GiveBody, user: dict = Depends(get_current_user)):
    role = user.get("role") or ""
    if role not in GIVERS:
        raise HTTPException(403, "Only managers give leads by hand")
    for value in (conversation_id, body.agent_id):
        try:
            uuid.UUID(value)
        except ValueError:
            raise HTTPException(400, "Unknown chat or person")
    if role == "supervisor":
        conv = await db.get_conversation_simple(conversation_id)
        if not conv:
            raise HTTPException(404, "Chat not found")
        team_ids = set(await db.get_team_ids_for_supervisor(user.get("id", "")) or [])
        if conv.get("team_id") not in team_ids:
            raise HTTPException(403, "Outside your team")
    agents = await db.get_operator_rows([body.agent_id])
    if agents is None:
        raise HTTPException(502, "Couldn't read the team. Try again.")
    if not agents:
        raise HTTPException(404, "No agent with that id takes chats")
    try:
        followup = await lead_lottery.give_by_hand(conversation_id, agents[0], user)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    logger.info(f"[followups] {conversation_id} given by hand to {body.agent_id} role={role}")
    return {"success": True, "data": followup, "count": 1}
