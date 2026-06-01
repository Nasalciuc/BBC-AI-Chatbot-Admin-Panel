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

# Shared HTTP client for CRM — avoids TLS handshake per call
_crm_client: Optional[httpx.AsyncClient] = None


def _get_crm_client() -> httpx.AsyncClient:
    global _crm_client
    if _crm_client is None:
        _crm_client = httpx.AsyncClient(
            timeout=CRM_TIMEOUT,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )
    return _crm_client


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

    Uses get_missing_fields as single source of truth — same logic
    that drives the AI's "Still needed" display in VISITOR CONTEXT.
    CRM submits ONLY when the AI has nothing left to collect.
    """
    from app.models.lead import get_missing_fields

    conv = {
        "visitor_name": getattr(visitor, "name", None),
        "visitor_email": getattr(visitor, "email", None),
        "visitor_phone": getattr(visitor, "phone", None),
    }

    missing = get_missing_fields(lead, conv)
    return len(missing) == 0


def build_crm_payload(lead: dict, visitor, conv_metadata: dict | None = None, suid: str | None = None) -> dict:
    """Build CRM API request body from lead + visitor data."""
    origin = (lead.get("origin_code") or "").upper()
    dest = (lead.get("destination_code") or "").upper()
    departure = format_date_iso(lead.get("departure_date"))
    return_date = lead.get("return_date")

    trip_type = lead.get("trip_type") or ("round_trip" if return_date else "one_way")

    cabin = lead.get("cabin_class") or "business"
    cabin_map = {
        "business": "business",
        "first": "first",
        "premium_economy": "premium_economy",
        "premium economy": "premium_economy",
    }
    cabin_class = cabin_map.get(str(cabin).lower(), "business")

    pax = lead.get("passengers")
    if not pax:
        logger.warning(
            "CRM payload: passengers is None (check_crm_ready should have blocked)"
        )
        pax = 1
    if isinstance(pax, str):
        try:
            pax = int(pax)
        except ValueError:
            logger.warning(
                f"CRM payload: passengers='{pax}' not parseable, defaulting to 1"
            )
            pax = 1
    pax = max(1, min(9, int(pax)))

    _children = int(lead.get("children_count") or lead.get("_children_count") or 0)
    _infants = int(lead.get("infant_count") or lead.get("_infant_count") or 0)
    _adults = max(1, pax - _children - _infants)

    phone = format_phone_international(getattr(visitor, "phone", "") or "")

    flights = [{"from": origin, "to": dest, "date": departure}]
    if trip_type == "round_trip" and return_date:
        flights.append({
            "from": dest,
            "to": origin,
            "date": format_date_iso(return_date),
        })

    # UTM marketing attribution
    _meta = conv_metadata or {}
    _utm = {}
    if _meta.get("utm_source"):
        _utm["_utmsource"] = _meta["utm_source"]
    if _meta.get("utm_medium"):
        _utm["_utmmedium"] = _meta["utm_medium"]
    if _meta.get("utm_campaign"):
        _utm["_utmcampaign"] = _meta["utm_campaign"]
    if suid:
        _utm["suid"] = suid

    return {
        "trip_type": trip_type,
        "cabin_class": cabin_class,
        "client": {
            "name": (getattr(visitor, "name", "") or "Customer").strip(),
            "email": (getattr(visitor, "email", "") or "").lower().strip(),
            "phone": phone,
        },
        "passengers": {
            "adult": min(9, _adults),
            "child": min(9, _children),
            "infant": min(9, _infants),
        },
        "coupon": "",
        "flights": flights,
        "sms": False,
        **_utm,
    }


async def submit_to_crm(
    lead: dict, visitor, conversation_id: str,
    conv_metadata: dict | None = None,
    client_ip: str | None = None,
    suid: str | None = None,
) -> CRMResult:
    """Submit flight request to BBC CRM. Non-blocking errors."""
    if not settings.crm_api_url:
        return CRMResult(success=False, error="CRM not configured")

    payload = build_crm_payload(lead, visitor, conv_metadata=conv_metadata, suid=suid)
    endpoint = f"{settings.crm_api_url.rstrip('/')}{CRM_CHATBOT_ENDPOINT}"

    try:
        client = _get_crm_client()
        resp = await client.post(
            endpoint,
            json=payload,
            headers={
                "Content-Type": "application/json",
                **({"x-custom-client-ip": client_ip} if client_ip else {}),
            },
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
        _children = int(lead.get("children_count") or lead.get("_children_count") or 0)
        _infants = int(lead.get("infant_count") or lead.get("_infant_count") or 0)
        _adults = max(1, pax - _children - _infants)
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
            "passengers": {
                "adult": min(9, _adults),
                "child": min(9, _children),
                "infant": min(9, _infants),
            },
            "flights": flights,
            "sms": False,
        }

        # UTM from conversation metadata
        _ab_meta = conv.get("metadata") or {}
        if _ab_meta.get("utm_source"):
            payload["_utmsource"] = _ab_meta["utm_source"]
        if _ab_meta.get("utm_medium"):
            payload["_utmmedium"] = _ab_meta["utm_medium"]
        if _ab_meta.get("utm_campaign"):
            payload["_utmcampaign"] = _ab_meta["utm_campaign"]
        _ab_suid = conv.get("visitor_id")
        if _ab_suid:
            payload["suid"] = _ab_suid

        endpoint = f"{settings.crm_api_url.rstrip('/')}{CRM_CHATBOT_ENDPOINT}"
        cid = conv.get("id", "?")
        logger.info(f"[CRM-ABANDONED] conv={cid} {origin}->{dest} {dep_date}")

        client = _get_crm_client()
        resp = await client.post(
            endpoint,
            json=payload,
            headers={
                "Content-Type": "application/json",
                **({"x-custom-client-ip": _ab_meta.get("client_ip")} if _ab_meta.get("client_ip") else {}),
            },
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
