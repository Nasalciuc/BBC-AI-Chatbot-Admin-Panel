"""Operator "missing details" card (PR-7).

In human mode the bot stays silent, but the client often gives the route, date
or passenger count straight to the operator — and today nothing extracts it,
so the lead stays thin. On every human-mode client message this runs the same
regex extraction, merges it onto the lead, and pushes a lead_details event on
the operator-only SSE channel (op:{cid}) with what is known, what is missing
and the question to ask next. It never speaks to the client.

Behind EXTRACT_EVERY_MESSAGE and OPERATOR_DETAILS_CARD (both off at merge).
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def build_details_payload(lead: dict, conv: Optional[dict] = None) -> dict:
    from app.pipeline.slots import next_slot, question_for, ordered_missing

    lead = lead or {}
    notes = lead.get("trip_notes") if isinstance(lead.get("trip_notes"), dict) else {}
    missing = ordered_missing(lead)
    slot = next_slot(lead)
    return {
        "type": "lead_details",
        "route": (
            f"{lead.get('origin_code')} → {lead.get('destination_code')}"
            if lead.get("origin_code") and lead.get("destination_code") else None
        ),
        "departure_date": lead.get("departure_date"),
        "return_date": lead.get("return_date"),
        "trip_type": lead.get("trip_type"),
        "passengers": lead.get("passengers"),
        "notes": notes,
        "missing": missing,
        "suggested_question": question_for(slot),
    }


async def extract_and_push(conversation_id: str, message: str) -> None:
    """Extract from a human-mode client message, merge to the lead, push the card.

    Best-effort and silent: a failure must never disturb the operator chat.
    """
    from app.flags import flag

    if not flag("EXTRACT_EVERY_MESSAGE", False):
        return
    try:
        from app.pipeline.entity_extractor import extract_entities
        from app.services import lead_service
        from app.db import supabase as db

        extracted = extract_entities(message)
        entities = {
            "origin": extracted.origin_code,
            "destination": extracted.destination_code,
            "departure_date": extracted.departure_date,
            "return_date": extracted.return_date,
            "trip_type": extracted.trip_type,
            "passengers": extracted.passengers,
            "cabin_class": extracted.cabin_class,
            "_children_count": extracted.children_count or 0,
            "_infant_count": extracted.infant_count or 0,
        }
        entities = {k: v for k, v in entities.items() if v}
        if entities:
            await lead_service.update_lead_from_entities(conversation_id, entities)
        if not flag("OPERATOR_DETAILS_CARD", False):
            return
        lead = await lead_service.get_or_create_lead(conversation_id)
        from app.realtime.manager import manager
        await manager.push(f"op:{conversation_id}", build_details_payload(lead))
    except Exception as e:  # noqa: silent — the operator card is a convenience, never load-bearing
        logger.warning(f"[operator-card] extract/push failed for {conversation_id}: {type(e).__name__}: {e}")
