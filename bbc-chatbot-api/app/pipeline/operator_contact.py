"""The contact a client types to an operator.

With an operator in the chat Alex is silent, and the client's messages skip the
pipeline that records an email, a phone or a name (contact_capture, in the
orchestrator). From 16 Nov 18:00 to 17 Nov 21:40 UTC, 7 clients typed an email
in a chat an operator had taken, and 5 of those emails never reached the
conversation: not in the panel's contact fields, not in the counts, and with
no phone recorded the CRM sweep skips the chat. In chats with Alex alone, 7 of
7 typed emails were recorded.

record_contact reads the email and the phone the same way the pipeline does
(extract_entities), and the name only as the answer to the operator's own name
question (contact_capture), and writes them through the same
lead_service.update_lead_from_entities. It never speaks to the client and never
logs a value. It is not behind EXTRACT_EVERY_MESSAGE: that flag is the trip
details and the operator card (operator_card.py); a contact the client gave is
recorded either way.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Shown in /health: messages to an operator that carried a contact, and the
# reads that failed before the write (a failed write is counted by lead_writes).
OPERATOR_CONTACT_HEALTH: dict = {"found": 0, "failures": 0}


async def record_contact(conversation_id: str, message: str) -> None:
    """Record the email, the phone or the name in a client's message to an operator.

    Best-effort and silent: a failure must never disturb the operator chat.
    """
    try:
        from app.db import supabase as db
        from app.pipeline.contact_capture import (
            asks_for_last_name, asks_for_name, last_question, name_from_answer, name_to_store,
        )
        from app.pipeline.entity_extractor import extract_entities
        from app.services import lead_service

        extracted = extract_entities(message)
        found: dict = {"email": extracted.email, "phone": extracted.phone}
        # A name only when the message has the shape of one AND the operator has
        # just asked for it: free text never names the client (orchestrator).
        answer = name_from_answer(message)
        if answer:
            asked = last_question(await db.get_recent_messages(conversation_id, limit=10))
            if asks_for_name(asked):
                conv = await db.get_conversation_simple(conversation_id) or {}
                found["name"] = name_to_store(conv.get("visitor_name"), answer, asks_for_last_name(asked))
        found = {k: v for k, v in found.items() if v}
        if not found:
            return
        if found.get("phone"):
            # A number typed the local way is placed by the visit's country
            # (#3296: "04…" from Australia, typed to an operator).
            from app.services.phone_country import country_hint

            conv_meta = ((await db.get_conversation_simple(conversation_id)) or {}).get("metadata")
            found["_phone_country"] = country_hint(conv_meta if isinstance(conv_meta, dict) else None)
        OPERATOR_CONTACT_HEALTH["found"] += 1
        await lead_service.update_lead_from_entities(conversation_id, found)
        logger.info(f"[operator-contact] {conversation_id}: {', '.join(sorted(k for k in found if not k.startswith('_')))} from the client's message")
    except Exception as e:  # noqa: silent — logged by type only: the error text can carry the client's email or phone
        OPERATOR_CONTACT_HEALTH["failures"] += 1
        logger.warning(f"[operator-contact] {conversation_id}: not recorded ({type(e).__name__})")


def _reset_for_tests() -> None:
    OPERATOR_CONTACT_HEALTH.update({"found": 0, "failures": 0})
