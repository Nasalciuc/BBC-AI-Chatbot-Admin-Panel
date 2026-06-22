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


def _resolve_crm_base(site_id: str | None = None) -> str:
    """Resolve CRM base URL per site. Falls back to settings.crm_api_url (BBC)."""
    from app.ai.prompts import get_brand_vars
    return get_brand_vars(site_id).get("crm_url") or settings.crm_api_url

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
    """Check if we have enough fields for CRM submission.

    Uses get_missing_fields(for_crm=True): route + contact + passengers +
    departure_date. AI prompts use strict mode (for_crm=False) and keep
    collecting dates/pax. Defaults in build_crm_payload are for abandoned
    cron path only.
    """
    from app.models.lead import get_missing_fields

    conv = {
        "visitor_name": getattr(visitor, "name", None),
        "visitor_email": getattr(visitor, "email", None),
        "visitor_phone": getattr(visitor, "phone", None),
    }

    missing = get_missing_fields(lead, conv, for_crm=True)
    return len(missing) == 0


def build_crm_payload(
    lead: dict,
    visitor,
    conv_metadata: dict | None = None,
    suid: str | None = None,
    allow_defaults: bool = False,
) -> dict:
    """Build CRM API request body from lead + visitor data."""
    origin = (lead.get("origin_code") or "").upper()
    if allow_defaults and not origin:
        origin = "AAA"
    dest = (lead.get("destination_code") or "").upper()
    if allow_defaults and not dest:
        dest = "AAA"
    _dep = lead.get("departure_date")
    if not _dep:
        _dep = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        logger.info(f"CRM payload: departure_date defaulted to {_dep} (+30d)")
    departure = format_date_iso(_dep)
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
        if not allow_defaults:
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
    if _meta.get("utm_term"):
        _utm["utm_term"] = _meta["utm_term"]
    if _meta.get("utm_content"):
        _utm["utm_content"] = _meta["utm_content"]
    if _meta.get("gclid"):
        _utm["gclid"] = _meta["gclid"]
    if _meta.get("fbclid"):
        _utm["fbclid"] = _meta["fbclid"]
    if _meta.get("referrer"):
        _utm["http_referrer"] = _meta["referrer"]
    if _meta.get("google_analytics_client_id"):
        _utm["google_analytics_client_id"] = _meta["google_analytics_client_id"]
    if _meta.get("kayak_click_id"):
        _utm["kayak_click_id"] = _meta["kayak_click_id"]
    if suid:
        _utm["suid"] = suid

    payload = {
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
    # BCT CRM requires recaptchaToken (any non-empty string accepted)
    if (conv_metadata or {}).get("site") == "bct":
        payload["recaptchaToken"] = "chatbot"
    return payload


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
    _site = (conv_metadata or {}).get("site")
    _crm_base = _resolve_crm_base(_site)
    endpoint = f"{_crm_base.rstrip('/')}{CRM_CHATBOT_ENDPOINT}"
    logger.info(f"[{conversation_id}] CRM endpoint: {endpoint} (site={_site or 'default'})")

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
        phone = format_phone_international(conv.get("visitor_phone", ""))
        if not phone:
            return CRMResult(success=False, error="Phone invalid")

        from types import SimpleNamespace
        _v = SimpleNamespace(
            name=conv.get("visitor_name") or "",
            email=conv.get("visitor_email") or "",
            phone=conv.get("visitor_phone") or "",
        )
        payload = build_crm_payload(
            lead,
            _v,
            conv_metadata=conv.get("metadata"),
            suid=conv.get("visitor_id"),
            allow_defaults=True,
        )

        _ab_meta = conv.get("metadata") or {}
        _ab_site = _ab_meta.get("site")
        _crm_base = _resolve_crm_base(_ab_site)
        endpoint = f"{_crm_base.rstrip('/')}{CRM_CHATBOT_ENDPOINT}"
        cid = conv.get("id", "?")
        logger.info(f"[cron][{cid}] CRM endpoint: {endpoint} (site={_ab_site or 'default'})")
        _fl0 = (payload.get("flights") or [{}])[0]
        logger.info(
            f"[CRM-ABANDONED] conv={cid} {_fl0.get('from', '?')}->{_fl0.get('to', '?')} {_fl0.get('date', '?')}"
        )

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
