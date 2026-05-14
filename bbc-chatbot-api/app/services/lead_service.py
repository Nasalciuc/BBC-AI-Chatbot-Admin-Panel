"""Lead service — creation and updates. Score calculated EXCLUSIVELY in Python."""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _calculate_score(lead: dict, conv: Optional[dict] = None) -> int:
    """
    Calculate lead score 0-100.
    Mirrors the formula in app_settings (score_weight_*).
    EXCLUSIVELY calculated in Python — NOT in a SQL trigger.
    """
    score = 0
    has_name  = bool(lead.get("visitor_name")  or (conv and conv.get("visitor_name")))
    has_email = bool(lead.get("visitor_email") or (conv and conv.get("visitor_email")))
    has_phone = bool(lead.get("visitor_phone") or (conv and conv.get("visitor_phone")))
    has_route = bool(lead.get("origin_code") and lead.get("destination_code"))
    has_dates = bool(lead.get("departure_date"))
    has_pax   = bool(lead.get("passengers"))
    if has_name:   score += 20
    if has_phone:  score += 15
    if has_email:  score += 10
    if has_route:  score += 15
    if has_dates:  score += 10
    if has_pax:    score += 10
    return min(score, 100)


async def get_or_create_lead(conversation_id: str) -> Optional[dict]:
    try:
        from app.db.supabase import get_client, _run_sync
        db_client = get_client()
        res = await _run_sync(lambda: db_client.table("leads").select("*").eq("conversation_id", conversation_id).limit(1).execute())
        return res.data[0] if res.data else None  # type: ignore[index]
    except Exception as e:
        logger.error(f"get_or_create_lead error: {e}")
        return None


async def update_lead_from_entities(conversation_id: str, entities: dict) -> None:
    """
    Update lead with entities extracted by AI.
    Creates lead if it doesn't exist and we have minimum useful data.
    """
    try:
        from app.db.supabase import get_client, _run_sync
        db_client = get_client()

        res = await _run_sync(lambda: db_client.table("leads").select("id,score").eq("conversation_id", conversation_id).limit(1).execute())
        if not res.data:
            has_useful = any(entities.get(k) for k in ["name", "email", "phone", "origin", "destination", "departure_date"])
            if not has_useful:
                return
            lead_res = await _run_sync(lambda: db_client.table("leads").insert({"conversation_id": conversation_id}).execute())
            if not lead_res.data:
                return
            lead_id = lead_res.data[0]["id"]  # type: ignore[index]
        else:
            lead_id = res.data[0]["id"]  # type: ignore[index]

        # Update conversation with contact info
        conv_payload: dict = {}
        if entities.get("name"):   conv_payload["visitor_name"]  = entities["name"]
        if entities.get("email"):  conv_payload["visitor_email"] = entities["email"]
        if entities.get("phone"):  conv_payload["visitor_phone"] = entities["phone"]
        if conv_payload:
            await _run_sync(lambda: db_client.table("conversations").update(conv_payload).eq("id", conversation_id).execute())

        # Update lead with route/travel entities
        lead_payload: dict = {}
        if entities.get("origin"):       lead_payload["origin_code"]      = entities["origin"]
        if entities.get("destination"):  lead_payload["destination_code"] = entities["destination"]
        if entities.get("passengers"):      lead_payload["passengers"]       = entities["passengers"]
        if entities.get("cabin_class"):     lead_payload["cabin_class"]      = entities["cabin_class"]
        if entities.get("departure_date"):  lead_payload["departure_date"]   = entities["departure_date"]
        if entities.get("return_date"):     lead_payload["return_date"]      = entities["return_date"]
        if entities.get("trip_type"):       lead_payload["trip_type"]        = entities["trip_type"]
        # Set human-readable route display for handoff messages
        if entities.get("origin") and entities.get("destination"):
            lead_payload["route_display"] = f"{entities['origin']} → {entities['destination']}"
        if lead_payload:
            await _run_sync(lambda: db_client.table("leads").update(lead_payload).eq("id", lead_id).execute())

        # Recalculate score
        lead_res = await _run_sync(lambda: db_client.table("leads").select("*").eq("id", lead_id).single().execute())
        conv_res = await _run_sync(lambda: db_client.table("conversations").select("visitor_name,visitor_email,visitor_phone").eq("id", conversation_id).single().execute())
        lead_data = lead_res.data or {}  # type: ignore[assignment]
        conv_data = conv_res.data or {}  # type: ignore[assignment]
        new_score = _calculate_score(lead_data, conv_data)
        new_tier  = "gold" if new_score >= 80 else "silver" if new_score >= 50 else "bronze"
        await _run_sync(lambda: db_client.table("leads").update({"score": new_score, "tier": new_tier}).eq("id", lead_id).execute())
    except Exception as e:
        logger.error(f"update_lead_from_entities error: {e}")
