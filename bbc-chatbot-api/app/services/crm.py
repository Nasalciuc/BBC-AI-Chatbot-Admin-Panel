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
from datetime import datetime, timedelta, timezone
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
    """Format phone to E.164: +{country}{number} — digits only after +.

    Handles all known broken formats:
      '1+2108844081'      → '+12108844081'
      '+1+2108844081'     → '+12108844081'
      '+1 (210) 884-4081'  → '+12108844081'
      '2108844081'        → '+12108844081' (assume US)
      '+442079460958'      → '+442079460958'
      '0044207946'        → '+44207946' (strip intl prefix 00)
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if not digits or len(digits) < 7:
        return ""
    if digits.startswith("00") and len(digits) > 2:
        digits = digits[2:]
    if len(digits) == 10:
        digits = f"1{digits}"
    return f"+{digits}"


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


async def submit_abandoned_to_crm(conv: dict, lead: dict | None) -> CRMResult:
    """Submit abandoned conv to CRM. Bypasses check_crm_ready, uses defaults for missing route."""
    if not settings.crm_api_url:
        return CRMResult(success=False, error="CRM URL not configured")

    try:
        lead = lead or {}
        origin = (lead.get("origin_code") or "AAA").upper()[:3]
        dest = (lead.get("destination_code") or "AAA").upper()[:3]
        dep_date = lead.get("departure_date")
        ret_date = lead.get("return_date")
        cabin = lead.get("cabin_class") or "business"
        pax = lead.get("passengers") or 1
        if isinstance(pax, str):
            try:
                pax = int(pax)
            except ValueError:
                pax = 1
        pax = max(1, min(9, int(pax)))
        trip_type = "round_trip" if ret_date else "one_way"

        if not dep_date:
            dep_date = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        else:
            dep_date = format_date_iso(dep_date)

        phone = format_phone_international(conv.get("visitor_phone", ""))
        if not phone:
            return CRMResult(success=False, error="Phone invalid")

        flights = [{"from": origin, "to": dest, "date": str(dep_date)}]
        if trip_type == "round_trip" and ret_date:
            flights.append({
                "from": dest,
                "to": origin,
                "date": format_date_iso(ret_date),
            })

        cabin_map = {
            "business": "business",
            "first": "first",
            "premium_economy": "premium_economy",
        }
        payload = {
            "trip_type": trip_type,
            "cabin_class": cabin_map.get(str(cabin).lower(), "business"),
            "coupon": "",
            "client": {
                "name": (conv.get("visitor_name") or "Customer").strip(),
                "email": (conv.get("visitor_email") or "").lower().strip(),
                "phone": phone,
            },
            "passengers": {"adult": pax, "child": 0, "infant": 0},
            "flights": flights,
            "sms": False,
        }

        endpoint = f"{settings.crm_api_url.rstrip('/')}{CRM_CHATBOT_ENDPOINT}"
        cid = conv.get("id", "?")
        logger.info(f"[CRM-ABANDONED] conv={cid} {origin}->{dest} {dep_date}")

        async with httpx.AsyncClient(timeout=CRM_TIMEOUT) as client:
            resp = await client.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") is False:
                logger.error(f"[CRM-ABANDONED] FAIL conv={cid} body success=false: {resp.text[:300]}")
                return CRMResult(success=False, error="CRM returned success=false")
            req_id = data.get("data", {}).get("id", "")
            logger.info(f"[CRM-ABANDONED] OK conv={cid} crm_id={req_id}")
            return CRMResult(success=True, request_id=req_id)

        body = resp.text[:300]
        logger.error(f"[CRM-ABANDONED] FAIL conv={cid} status={resp.status_code} body={body}")
        return CRMResult(success=False, error=f"HTTP {resp.status_code}: {body}")

    except httpx.TimeoutException:
        return CRMResult(success=False, error="Timeout")
    except Exception as e:
        return CRMResult(success=False, error=str(e))
