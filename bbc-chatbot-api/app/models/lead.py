"""Lead schemas — aligned with DB schema V10 leads table.
Single source of truth for lead scoring: lead_service.py._calculate_score()
"""

from typing import Optional


def get_lead_tier(score: int) -> str:
    """Convert numeric score to tier label."""
    if score >= 80:
        return "gold"
    if score >= 50:
        return "silver"
    return "bronze"


def get_missing_fields(lead_dict: dict, conv_dict: Optional[dict] = None, *, for_crm: bool = False) -> list[str]:
    """Return list of travel fields the AI should collect next.

    Contact info (name/email/phone) checked via conv_dict (from visitor form).
    Travel info (route/dates/passengers) checked via lead_dict (from entity extraction).
    Priority: route → dates → trip type → passengers → contact fallback.
    """
    conv = conv_dict or {}
    missing: list[str] = []

    # Travel info — from lead (entity extraction, accumulated)
    has_route = bool(
        lead_dict.get("origin_code") and lead_dict.get("destination_code")
    )
    has_departure = bool(lead_dict.get("departure_date"))
    # BBC rule: any confirmed trip_type counts (consultant clarifies details)
    has_return_or_oneway = bool(
        lead_dict.get("return_date")
        or lead_dict.get("trip_type") in ("one_way", "round_trip", "multi_city", "open_jaw")
    )
    has_passengers = bool(lead_dict.get("passengers"))

    # Contact info — from visitor form (conv_dict) OR lead
    has_name = bool(
        lead_dict.get("visitor_name") or conv.get("visitor_name")
    )
    has_email = bool(
        lead_dict.get("visitor_email") or conv.get("visitor_email")
    )
    has_phone = bool(
        lead_dict.get("visitor_phone") or conv.get("visitor_phone")
    )

    # Priority: travel data first (contact usually from form)
    if not has_route:
        missing.append("route (origin and destination)")
    if not for_crm:
        # Strict mode: AI prompts need all fields for collection
        if not has_departure:
            missing.append("departure date")
        if has_departure and not has_return_or_oneway:
            missing.append("return date or one-way confirmation")
        if not has_passengers:
            missing.append("number of travelers (adults, children, infants)")
    # CRM mode (for_crm=True): route + contact is sufficient
    # departure/passengers/trip_type → defaults in build_crm_payload
    # Contact info — only if NOT provided via form
    if not has_name:
        missing.append("name")
    if not has_email:
        missing.append("email")
    if not has_phone:
        missing.append("phone number")

    return missing
