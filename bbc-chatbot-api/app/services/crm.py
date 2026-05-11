"""
CRM Integration — auto-submit flight requests from chatbot conversations.

When chatbot collects all required data (contact + route + date),
creates a Flight Request in BBC CRM system automatically.

API: POST webapi.buybusinessclass.com/requests/chatbot
Auth: none required (confirmed via testing).
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

CRM_CHATBOT_ENDPOINT = "/requests/chatbot"
CRM_TIMEOUT = 10


@dataclass
class CRMResult:
    success: bool
    request_id: str = ""
    error: str = ""


def format_phone_international(phone: str) -> str:
    """Ensure phone matches CRM regex ^\\+[\\d]+$."""
    if not phone:
        return ""
    cleaned = re.sub(r"[^\d+]", "", phone)
    if cleaned.startswith("+"):
        return cleaned
    if cleaned.startswith("1") and len(cleaned) == 11:
        return f"+{cleaned}"
    if len(cleaned) == 10:
        return f"+1{cleaned}"
    if cleaned:
        return f"+{cleaned}"
    return cleaned


def format_date_iso(date_str) -> str:
    """Ensure date is YYYY-MM-DD for CRM API. Safety net — dates should already be ISO from extractor."""
    if not date_str:
        return ""
    s = str(date_str)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    # If it's a date object, convert
    if hasattr(date_str, "isoformat"):
        return date_str.isoformat()
    return s


def check_crm_ready(lead: dict, visitor) -> bool:
    """Check if we have ALL required fields for CRM submission.

    Contact info comes from visitor (form data).
    Route info comes from lead (accumulated across messages).
    """
    # Contact — from visitor form
    if not getattr(visitor, "name", None) or len(visitor.name.strip()) < 2:
        return False
    if not getattr(visitor, "email", None) or "@" not in visitor.email:
        return False
    if not getattr(visitor, "phone", None) or len(visitor.phone.strip()) < 7:
        return False

    # Route — from lead (accumulated)
    origin = lead.get("origin_code") or ""
    dest = lead.get("destination_code") or ""
    if len(origin) != 3 or len(dest) != 3:
        return False

    # Date — from lead
    if not lead.get("departure_date"):
        return False

    return True


def build_crm_payload(lead: dict, visitor) -> dict:
    """Build CRM API request body from lead + visitor data."""
    origin = (lead.get("origin_code") or "").upper()
    dest = (lead.get("destination_code") or "").upper()
    departure = format_date_iso(lead.get("departure_date"))
    return_date = lead.get("return_date")

    trip_type = "round_trip" if return_date else "one_way"

    cabin = lead.get("cabin_class") or "business"
    cabin_map = {
        "business": "business",
        "first": "first",
        "premium_economy": "premium_economy",
        "premium economy": "premium_economy",
    }
    cabin_class = cabin_map.get(str(cabin).lower(), "business")

    pax = lead.get("passengers") or 1
    if isinstance(pax, str):
        try:
            pax = int(pax)
        except ValueError:
            pax = 1
    pax = max(1, min(9, int(pax)))

    phone = format_phone_international(getattr(visitor, "phone", "") or "")

    flights = [{"from": origin, "to": dest, "date": departure}]
    if trip_type == "round_trip" and return_date:
        flights.append({
            "from": dest,
            "to": origin,
            "date": format_date_iso(return_date),
        })

    return {
        "trip_type": trip_type,
        "cabin_class": cabin_class,
        "client": {
            "name": (getattr(visitor, "name", "") or "Customer").strip(),
            "email": (getattr(visitor, "email", "") or "").lower().strip(),
            "phone": phone,
        },
        "passengers": {
            "adult": pax,
            "child": 0,
            "infant": 0,
        },
        "coupon": "",
        "flights": flights,
        "sms": False,
    }


async def submit_to_crm(lead: dict, visitor, conversation_id: str) -> CRMResult:
    """Submit flight request to BBC CRM. Non-blocking errors."""
    if not settings.crm_api_url:
        return CRMResult(success=False, error="CRM not configured")

    payload = build_crm_payload(lead, visitor)
    endpoint = f"{settings.crm_api_url.rstrip('/')}{CRM_CHATBOT_ENDPOINT}"

    try:
        async with httpx.AsyncClient(timeout=CRM_TIMEOUT) as client:
            resp = await client.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

        if resp.status_code == 200:
            data = resp.json()
            request_id = data.get("data", {}).get("id", "")
            logger.info(
                f"CRM OK conv={conversation_id} "
                f"crm_id={request_id} "
                f"route={payload['flights'][0]['from']}->{payload['flights'][0]['to']}"
            )
            return CRMResult(success=True, request_id=request_id)

        body = resp.text[:300]
        logger.error(f"CRM FAIL conv={conversation_id} status={resp.status_code} body={body}")
        return CRMResult(success=False, error=f"HTTP {resp.status_code}")

    except httpx.TimeoutException:
        logger.error(f"CRM TIMEOUT conv={conversation_id}")
        return CRMResult(success=False, error="Timeout")
    except Exception as e:
        logger.error(f"CRM ERROR conv={conversation_id}: {e}")
        return CRMResult(success=False, error=str(e))
