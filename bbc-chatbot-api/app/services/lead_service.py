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
    """Update lead with entities extracted by AI. Optimized: 3-4 RT instead of 7."""
    try:
        from app.db.supabase import get_client, _run_sync
        db_client = get_client()

        # RT 1: Select lead (travel fields only — visitor_* live on conversations)
        res = await _run_sync(
            lambda: db_client.table("leads")
            .select(
                "id,score,origin_code,destination_code,departure_date,"
                "passengers,cabin_class,return_date,trip_type,"
                "children_count,infant_count"
            )
            .eq("conversation_id", conversation_id)
            .limit(1)
            .execute()
        )

        if not res.data:
            has_useful = any(
                entities.get(k)
                for k in [
                    "name", "email", "phone", "origin", "destination",
                    "departure_date", "return_date", "trip_type", "passengers", "cabin_class",
                ]
            )
            if not has_useful:
                return
            # RT 2: Insert lead
            lead_res = await _run_sync(
                lambda: db_client.table("leads")
                .insert({"conversation_id": conversation_id})
                .execute()
            )
            if not lead_res.data:
                return
            lead_id = lead_res.data[0]["id"]  # type: ignore[index]
            lead_data = lead_res.data[0]  # type: ignore[index]
        else:
            lead_id = res.data[0]["id"]  # type: ignore[index]
            lead_data = res.data[0]  # type: ignore[index]

        conv_payload: dict = {}
        if entities.get("name"):
            conv_payload["visitor_name"] = entities["name"]
        if entities.get("email"):
            conv_payload["visitor_email"] = entities["email"]
        if entities.get("phone"):
            from app.services.crm import format_phone_international
            conv_payload["visitor_phone"] = format_phone_international(entities["phone"])

        lead_payload: dict = {}
        if entities.get("origin"):
            lead_payload["origin_code"] = entities["origin"]
        if entities.get("destination"):
            lead_payload["destination_code"] = entities["destination"]
        if entities.get("passengers"):
            lead_payload["passengers"] = entities["passengers"]
        if entities.get("cabin_class"):
            lead_payload["cabin_class"] = entities["cabin_class"]
        if entities.get("departure_date"):
            lead_payload["departure_date"] = entities["departure_date"]
        if entities.get("return_date"):
            lead_payload["return_date"] = entities["return_date"]
        if entities.get("trip_type"):
            lead_payload["trip_type"] = entities["trip_type"]
        if entities.get("_children_count"):
            lead_payload["children_count"] = entities["_children_count"]
        if entities.get("_infant_count"):
            lead_payload["infant_count"] = entities["_infant_count"]
        if entities.get("origin") and entities.get("destination"):
            lead_payload["route_display"] = f"{entities['origin']} → {entities['destination']}"

        # RT 2-3: Update conv + lead in parallel (when both have data)
        tasks = []
        if conv_payload:
            tasks.append(
                _run_sync(
                    lambda: db_client.table("conversations")
                    .update(conv_payload)
                    .eq("id", conversation_id)
                    .execute()
                )
            )
        if lead_payload:
            tasks.append(
                _run_sync(
                    lambda: db_client.table("leads")
                    .update(lead_payload)
                    .eq("id", lead_id)
                    .execute()
                )
            )
        if tasks:
            await asyncio.gather(*tasks)

        # Score from merged local state (no re-select lead + conv)
        merged_lead = {**lead_data, **lead_payload}
        merged_conv = {
            "visitor_name": conv_payload.get("visitor_name") or entities.get("name"),
            "visitor_email": conv_payload.get("visitor_email") or entities.get("email"),
            "visitor_phone": conv_payload.get("visitor_phone"),
        }
        if not merged_conv.get("visitor_phone") and entities.get("phone"):
            from app.services.crm import format_phone_international
            merged_conv["visitor_phone"] = format_phone_international(entities["phone"])

        new_score = _calculate_score(merged_lead, merged_conv)
        new_tier = "gold" if new_score >= 80 else "silver" if new_score >= 50 else "bronze"

        if new_score != lead_data.get("score"):
            await _run_sync(
                lambda: db_client.table("leads")
                .update({"score": new_score, "tier": new_tier})
                .eq("id", lead_id)
                .execute()
            )

    except Exception as e:
        logger.error(f"update_lead_from_entities error: {e}")
