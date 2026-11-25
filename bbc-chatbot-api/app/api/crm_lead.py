"""The agent's "Create lead in CRM" button (app/services/crm_leads_api.py, CRM's CHAT-LEADS-API v1.1).

GET  /api/conversations/{id}/crm-lead   what the panel shows under the chat: nothing (switch off), the lead the
                                        button already made (number, link, warnings), the button, or why not yet.
POST /api/conversations/{id}/crm-lead   makes it: one call to the CRM; the answer is kept on the chat
                                        (metadata.crm_lead) and the lead row is marked "in the CRM", so the old
                                        sweep and the backstop never send this client a second time.

A chat the old path already sent (the lead row is "in the CRM": the sweep, the backstop, Send to CRM) gets no
button and no call: `lead` stays null, `can_create` false and `reason` says it is in the CRM, with its number when
there is one (the doc's D4: blocked until the CRM team says what a second call does to such a lead).

Who: the agent who has the chat (given to them, closed by them, or the one who talked in it). A manager (owner,
admin, dev) may press it on any chat an agent has had: the lead still goes to that agent, never to the manager.
A chat nobody has taken (the shared line, the assistant alone) answers "Take the chat first", as the CRM asks.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import supabase as db
from app.security.auth import get_current_user
from app.services import crm_leads_api as api
from app.services.conversation_lock import LockBusy
from app.services.lead_access import MANAGERS, owner_of_conversation

logger = logging.getLogger(__name__)

router = APIRouter()

_ROLES = (*MANAGERS, "sales")


class CrmLeadRequest(BaseModel):
    # The agent's own words for the CRM (the CRM's `note`, 2000 characters at most).
    note: Optional[str] = Field(default=None, max_length=api.NOTE_MAX)


def _first(conv: dict) -> str:
    name = str(conv.get("visitor_name") or "").strip()
    return name.split()[0] if name else "the client"


def _agent_for(user: dict, conv: dict) -> tuple[Optional[str], Optional[str]]:
    """(the panel user the lead goes to, None), or (None, why the button can't be used on this chat)."""
    owner = owner_of_conversation(conv)
    if (user.get("role") or "") == "sales":
        if owner and owner == user.get("id"):
            return owner, None
        if owner:
            return None, "This chat is another agent's."
        return None, "Take the chat first: the lead goes to the agent who has it."
    if owner:
        return owner, None
    return None, "No agent has had this chat yet: the lead goes to the agent who takes it."


def _no_contact(conv: dict) -> str:
    if conv.get("status") == "closed":
        return "No phone or e-mail: the CRM needs one of the two."
    return f"No phone or e-mail yet: ask {_first(conv)} in the chat."


def _sent_before(lead: Optional[dict]) -> Optional[str]:
    """The reason under the chat when the old path already put its lead in the CRM; None when it did not."""
    if not isinstance(lead, dict) or not lead.get("created_in_crm"):
        return None
    crm_id = str(lead.get("crm_lead_id") or "").strip()
    return f"Already in the CRM as lead {crm_id}." if crm_id else "Already in the CRM."


def _answer(enabled: bool, lead: Optional[dict], can_create: bool, reason: Optional[str]) -> dict:
    return {
        "success": True,
        "data": {"enabled": enabled, "lead": lead, "can_create": can_create, "reason": reason},
        "count": 1,
    }


def _http_status(call: api.CrmLeadCall) -> int:
    if call.error_code in ("NOT_CONFIGURED", "NOT_HTTPS", "LEASE_LOST"):
        return 503
    if call.error_code in ("AUTH_INVALID", "IP_NOT_ALLOWED"):
        return 502
    if call.status == 429:
        return 429
    if call.status >= 500 or call.error_code in ("TIMEOUT", "NETWORK", "NO_ANSWER", "BAD_ANSWER"):
        return 502
    return 422


def _http_message(call: api.CrmLeadCall) -> str:
    if call.status == 429:
        wait = f"in {call.retry_after} seconds" if call.retry_after else "in a minute"
        return f"The CRM is busy. Try again {wait}."
    return call.message or "The CRM refused the lead."


@router.get("/conversations/{conversation_id}/crm-lead")
async def crm_lead_status(conversation_id: str, user: dict = Depends(get_current_user)):
    """What the panel shows under the chat. Off for this user: enabled=false, the panel keeps "Send to CRM"."""
    if (user.get("role") or "") not in _ROLES or not api.enabled_for(user):
        return _answer(False, None, False, None)
    conv = await db.get_conversation_simple(conversation_id)
    if conv is None:
        # None is a failed read as well as no chat: either way there is nothing to show yet (counted).
        api.CRM_LEADS_API_HEALTH["status_read_failed"] += 1
        logger.warning(f"[crm-leads-api] status conv={conversation_id}: the chat could not be read")
        raise HTTPException(503, "Couldn't check this chat. Try again in a moment.")
    stored = api.stored_record(conv)
    if stored:
        return _answer(True, stored, False, None)
    receipts = await db.get_crm_receipts(conversation_id)
    if receipts is None:
        api.CRM_LEADS_API_HEALTH["status_read_failed"] += 1
        logger.warning(f"[crm-leads-api] status conv={conversation_id}: the lead's CRM mark could not be read")
        raise HTTPException(503, "Couldn't check this chat. Try again in a moment.")
    # Read a moment after the chat: a lead the button made in between is here, with its link (CodeRabbit #280).
    if api.is_receipt(receipts.get("crm_lead")):
        return _answer(True, receipts["crm_lead"], False, None)
    sent = _sent_before(receipts)
    if sent:
        return _answer(True, None, False, sent)
    _agent, why = _agent_for(user, conv)
    if why:
        # Not this user's chat (or nobody's yet): no button and nothing to say under it.
        return _answer(True, None, False, None)
    if not api.has_contact(conv):
        return _answer(True, None, False, _no_contact(conv))
    return _answer(True, None, True, None)


@router.post("/conversations/{conversation_id}/crm-lead")
async def create_crm_lead(
    conversation_id: str,
    body: Optional[CrmLeadRequest] = Body(default=None),
    confirm_locked: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    """Create the lead in the CRM for the agent who has this chat. Safe to press twice: the CRM keys the lead on
    the conversation, and a lead already made here is answered from the chat without a call."""
    if (user.get("role") or "") not in _ROLES:
        raise HTTPException(403, "Not authorized to create leads in the CRM")
    if not api.enabled_for(user):
        raise HTTPException(403, "The CRM button isn't on for you yet.")
    # One CRM submit at a time per chat: the old sweep and the backstop hold the same lock around theirs, and the
    # chat is read inside it, so whichever goes second finds the other's receipt (CodeRabbit #280).
    try:
        async with api.submit_lock(conversation_id) as lease:
            return await _create(conversation_id, body, confirm_locked, user, lease)
    except LockBusy as e:
        # Another process is sending this chat to the CRM right now, or Redis could not say it is not: nothing was
        # sent from here.
        api.CRM_LEADS_API_HEALTH["busy"] += 1
        logger.warning(f"[crm-leads-api] conv={conversation_id}: not sent, {e}")
        if e.reason == "held":
            raise HTTPException(503, "This chat is being sent to the CRM right now. Try again in a minute.") from e
        raise HTTPException(503, "Couldn't make sure this chat isn't sent twice. Try again in a minute.") from e


async def _create(
    conversation_id: str, body: Optional[CrmLeadRequest], confirm_locked: bool, user: dict, lease
) -> dict:
    conv: Optional[dict] = None
    try:
        conv = await db.get_conversation(conversation_id, raise_on_error=True)
    except Exception as e:
        api.CRM_LEADS_API_HEALTH["status_read_failed"] += 1
        logger.error(f"[crm-leads-api] create conv={conversation_id}: the chat could not be read ({type(e).__name__}: {e})")
        raise HTTPException(503, "Couldn't read this chat. Try again in a moment.") from e
    if not conv:
        raise HTTPException(404, "Conversation not found")

    stored = api.stored_record(conv)
    if stored:
        return _answer(True, stored, False, None)
    # Sent by the old path already: answered from its mark, before any call (the CRM's own dedup is not relied on).
    sent = _sent_before(conv.get("lead") if isinstance(conv.get("lead"), dict) else None)
    if sent:
        return _answer(True, None, False, sent)

    agent_user_id, why = _agent_for(user, conv)
    if why or not agent_user_id:
        raise HTTPException(403, why or "No agent has had this chat yet.")
    if not api.has_contact(conv):
        api.CRM_LEADS_API_HEALTH["no_contact"] += 1
        raise HTTPException(422, _no_contact(conv))

    from app.security.behavior import is_locked

    if is_locked(conv.get("metadata")) and not confirm_locked:
        # The panel asks, then sends again with confirm_locked (same rule as Send to CRM, PR-15).
        raise HTTPException(409, "This conversation was locked for abuse. Confirm to create the lead anyway.")

    # The agent as the CRM knows them: their CRM id when they came in from the CRM (only for their own press),
    # and their company e-mail always.
    agent = await db.get_user_by_id(agent_user_id)
    agent_email = api.valid_email((agent or {}).get("email"))
    agent_id = user.get("crm_sub") if agent_user_id == user.get("id") else None
    if not agent_email and not agent_id:
        api.count_error("AGENT_UNREAD")
        logger.error(f"[crm-leads-api] conv={conversation_id}: agent {agent_user_id} has no readable e-mail")
        raise HTTPException(503, "Couldn't read the agent's account. Try again in a moment.")

    payload = api.build_payload(conv, agent_email=agent_email, agent_id=agent_id, note=body.note if body else None)
    # The lock is renewed while this runs; if Redis says it is no longer ours, nothing more leaves for the CRM.
    call = await api.post_lead(payload, still_held=lambda: not lease.lost)
    if not call.ok:
        raise HTTPException(_http_status(call), _http_message(call))

    record = api.record_of(call, agent_user_id=agent_user_id, by=user.get("id"), site=payload["site"])
    await _keep(conversation_id, conv, record, user)
    return _answer(True, record, False, None)


async def _keep(conversation_id: str, conv: dict, record: dict, user: dict) -> None:
    """The lead exists in the CRM: keep its number on the chat and mark the lead row, so nothing sends it again.
    Neither write can undo the lead; one that fails is counted and logged loudly (the panel still gets the link)."""
    frid = record.get("flight_request_id")
    saved = await db.patch_conversation_presence(conversation_id, {"crm_lead": record})
    if not saved:
        api.CRM_LEADS_API_HEALTH["save_failed"] += 1
        logger.error(f"[crm-leads-api] conv={conversation_id}: lead {frid} made in the CRM but not kept on the chat")

    lead: Optional[dict] = conv.get("lead") if isinstance(conv.get("lead"), dict) else None
    if lead is None:
        from app.services.lead_service import get_or_create_lead

        lead = await get_or_create_lead(conversation_id)
    if lead and lead.get("created_in_crm"):
        # Already sent by the old path (sweep, confirm, backstop): it stays marked with that receipt; this lead's
        # number lives in metadata.crm_lead.
        return
    marked: Optional[dict] = None
    if lead and lead.get("id"):
        marked = await db.mark_lead_created_in_crm(
            lead["id"], crm_lead_id=str(frid), source="push", set_by=user.get("id")
        )
    if marked is None:
        api.CRM_LEADS_API_HEALTH["mark_failed"] += 1
        logger.error(
            f"[crm-leads-api] conv={conversation_id}: lead {frid} made in the CRM but the lead row is not marked"
            f" ({'no lead row' if not lead else 'mark failed'})"
        )
        return
    db.invalidate_leads_universe()
