"""Who may act on a lead: the CRM routes and the lead edit.

Hole B (found 21 Nov 2026, PROBLEME.md #6): PATCH /leads/{id}/crm-id and /mark-crm-created checked the
role only, so any sales agent could paste a CRM id on, or push to the CRM, a lead another agent was
working, and take its credit. The rule now:

  - owner, admin, dev: any lead;
  - an agent (sales): a lead whose chat is theirs (given to them, closed by them, taken by them, or
    given by the follow-up lottery), or one nobody has while the lottery is off (today's practice:
    an AI-only lead anyone may pick up). Never another agent's, never one waiting blind in the shared
    line, and, with the lottery on, never one waiting for the lottery;
  - anyone else: what the route's own role check allows (unchanged).

Every refusal is counted (LEAD_ACCESS_HEALTH) and logged by ids only.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

MANAGERS = ("owner", "admin", "dev")

LEAD_ACCESS_HEALTH: dict[str, int] = {"refused_other_agent": 0, "refused_blind": 0, "refused_lottery": 0,
                                      "allowed_unowned": 0, "read_failed": 0}


def owner_of_conversation(conv: Optional[dict]) -> Optional[str]:
    """The agent a chat belongs to, by what the chat itself says."""
    if not conv:
        return None
    meta = conv.get("metadata") if isinstance(conv.get("metadata"), dict) else {}
    followup = meta.get("followup") if isinstance(meta.get("followup"), dict) else {}
    given = followup.get("agent_id") if followup.get("state") in ("pending", "acked", "manual") else None
    return conv.get("assigned_agent_id") or meta.get("closed_by_agent_id") or meta.get("engaged_agent_id") or given


def refusal(user: dict, conv: Optional[dict]) -> Optional[str]:
    """None when this user may act on the lead of `conv`, else what the panel says."""
    from app.api.conversations import _taken_blind
    from app.services import lead_lottery

    if (user.get("role") or "") != "sales":
        return None  # managers act on any lead; other roles are held by the route's own role check
    if conv and _taken_blind(user, conv):
        LEAD_ACCESS_HEALTH["refused_blind"] += 1
        return "Take the chat first: it's waiting for a person."
    owner = owner_of_conversation(conv)
    if owner and owner != user.get("id"):
        LEAD_ACCESS_HEALTH["refused_other_agent"] += 1
        return "This lead is another agent's."
    if not owner:
        if lead_lottery.enabled():
            LEAD_ACCESS_HEALTH["refused_lottery"] += 1
            return "This lead is waiting for the lottery to give it to someone."
        LEAD_ACCESS_HEALTH["allowed_unowned"] += 1
    return None


async def check(user: dict, lead: dict, what: str) -> None:
    """Raise 403 when the user may not act on this lead (503 when its chat cannot be read)."""
    from fastapi import HTTPException

    from app.db import supabase as db

    if (user.get("role") or "") != "sales":
        return
    conv_id = lead.get("conversation_id")
    try:
        conv = await db.get_conversation_simple(conv_id) if conv_id else None
    except Exception as e:
        LEAD_ACCESS_HEALTH["read_failed"] += 1
        logger.error(f"[lead-access] {what} lead={lead.get('id')}: chat not read, refused ({type(e).__name__}: {e})")
        raise HTTPException(503, "Couldn't check who this lead belongs to. Try again.") from e
    why = refusal(user, conv)
    if why:
        logger.warning(f"[lead-access] {what} refused: lead={lead.get('id')} user={user.get('id')}")
        raise HTTPException(403, why)
