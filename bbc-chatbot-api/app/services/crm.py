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


# ── Push truth (surfaced in /health as crm_pushes) ────────────
# #1259 pessaint: the CRM rejected a malformed email at validation, our
# flag went true anyway. State is written only on PROOF (2xx + id).
CRM_PUSH_HEALTH: dict = {
    "ok": 0,
    "failed": 0,
    "refused": 0,
    "last_error_at": None,
    "last_refusal_reason": None,
}


def _record_push(outcome: str, reason: str | None = None) -> None:
    CRM_PUSH_HEALTH[outcome] = CRM_PUSH_HEALTH.get(outcome, 0) + 1
    if outcome == "failed":
        CRM_PUSH_HEALTH["last_error_at"] = datetime.now(timezone.utc).isoformat()
    if outcome == "refused" and reason:
        CRM_PUSH_HEALTH["last_refusal_reason"] = reason


# ── Push quality gate ─────────────────────────────────────────
# Seen in pushed production data: test@ leads, score-0 shells, LHR→LHR,
# yahool.com/fomcast.net typo domains. The pipe pushed junk while gold
# rotted. Refusals are logged (INFO) and counted — never silently eaten.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
# Typo domains are REFUSED, never auto-corrected — we don't invent an
# address the client didn't give us.
_TYPO_DOMAINS = {
    "yahool.com", "fomcast.net", "gmial.com", "gamil.com", "hotmial.com",
    "yaho.com", "gnail.com", "hotmali.com", "outlok.com",
}


def crm_hygiene_gate(
    lead: dict, email: str, *, require_email: bool = True
) -> Optional[str]:
    """The HARD hygiene refusals — they hold on EVERY path (test@ emails,
    malformed/typo-domain email, origin==destination). require_email=False
    (the abandoned/no-engagement path) lets an email-less but phone-bearing
    contact through — the phone check lives in submit_abandoned_to_crm."""
    email = (email or "").lower().strip()
    if email:
        if not _EMAIL_RE.match(email):
            return "email_invalid"
        # Prefix only — a substring check would refuse "contest@gmail.com".
        if email.startswith("test@"):
            return "test_email"
        domain = email.rsplit("@", 1)[-1]
        if domain in _TYPO_DOMAINS:
            return "email_typo_domain"
    elif require_email:
        return "email_invalid"
    origin = (lead.get("origin_code") or "").upper()
    dest = (lead.get("destination_code") or "").upper()
    if origin and dest and origin == dest:
        return "same_route"
    return None


def crm_push_gate(lead: dict, email: str) -> Optional[str]:
    """Refusal reason for the STRICT (confirm-flow/button) push path:
    hygiene + the score floor. The abandoned/no-engagement path uses
    crm_hygiene_gate alone — BUSINESS RULE (owner, explicit): every
    captured contact is dialable, silent leads included; score never
    blocks a dialable human there."""
    reason = crm_hygiene_gate(lead, email, require_email=True)
    if reason:
        return reason
    if (lead.get("score") or 0) < 40:
        return "low_score"
    return None


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


def _agent_on_file(conv_metadata: dict) -> bool:
    """Does this client already have an operator attached? (commission splits)"""
    return bool(
        conv_metadata.get("engaged_agent_id")
        or conv_metadata.get("sticky_agent_id")
    )


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

    # What the chat learned about this client, for the consultant's call. Rides
    # as a custom top-level key like utm_term/gclid do; the panel shows the same
    # line on the lead regardless of how the CRM displays unknown keys.
    from app.services.lead_service import build_chat_context_line

    _signals = lead.get("intent_signals")
    _signals = dict(_signals) if isinstance(_signals, dict) else {}
    if _agent_on_file(_meta):
        _signals["returning_client"] = True
    _chat_context = build_chat_context_line(_signals)
    if _chat_context:
        _utm["chat_context"] = _chat_context

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
            if not request_id:
                # 200 with no id = the CRM's validation rejection shape
                # (#1259 pessaint). NOT a success — an id is the proof.
                logger.error(
                    f"CRM FAIL conv={conversation_id} status=200 without id "
                    f"body={resp.text[:300]}"
                )
                return CRMResult(success=False, error="no_id_in_response")
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


async def push_lead_to_crm(
    lead: dict,
    visitor,
    conversation_id: str,
    conv_metadata: dict | None = None,
    client_ip: str | None = None,
    suid: str | None = None,
) -> CRMResult:
    """THE push path: gate → submit → flag ONLY on proof (2xx + crm id).

    Every caller that wants created_in_crm=true goes through here — the
    panel's Push button and the orphan backstop. The flag, timestamp and
    crm_lead_id are written together on success and never otherwise.
    A gate refusal is VISIBLE state: the reason is stored on the lead
    (crm_push_gate_reason → the panel's "CRM pending" work-list) and
    counted in /health.crm_pushes; failures likewise counted, flag stays
    false."""
    from app.db import supabase as db

    reason = crm_push_gate(lead, getattr(visitor, "email", "") or "")
    if reason:
        logger.info(
            f"CRM push refused conv={conversation_id} lead={lead.get('id')} "
            f"reason={reason}"
        )
        _record_push("refused", reason)
        await db.update_lead_crm_push_state(lead["id"], gate_reason=reason)
        return CRMResult(success=False, error=f"gate:{reason}")

    result = await submit_to_crm(
        lead, visitor, conversation_id,
        conv_metadata=conv_metadata, client_ip=client_ip, suid=suid,
    )
    if result.success and result.request_id:
        marked = await db.mark_lead_created_in_crm(
            lead["id"], crm_lead_id=result.request_id
        )
        if marked is None:
            # The CRM row EXISTS but our flag write died. Reporting success
            # would strand the lead as an "orphan" the backstop re-pushes
            # every tick — a fresh CRM duplicate each time, uncapped.
            # Reporting failure makes the attempt cap apply.
            _record_push("failed")
            logger.error(
                f"CRM ACCEPTED conv={conversation_id} lead={lead.get('id')} "
                f"crm_id={result.request_id} but the flag write FAILED — "
                "returned as failure so retries stay attempt-capped"
            )
            return CRMResult(
                success=False,
                request_id=result.request_id,
                error="flag_write_failed",
            )
        _record_push("ok")
    else:
        _record_push("failed")
        logger.error(
            f"CRM push failed conv={conversation_id} lead={lead.get('id')} "
            f"error={result.error} — created_in_crm stays false"
        )
    return result


async def submit_abandoned_to_crm(conv: dict, lead: dict | None) -> CRMResult:
    """Submit abandoned conv to CRM. Bypasses check_crm_ready, uses defaults for missing route."""
    if not settings.crm_api_url:
        return CRMResult(success=False, error="CRM URL not configured")

    try:
        lead = lead or {}
        phone = format_phone_international(conv.get("visitor_phone", ""))
        if not phone:
            return CRMResult(success=False, error="Phone invalid")

        # Hygiene holds on THIS path too (score deliberately does not —
        # every contact is dialable). Refusals stay visible in /health.
        _hyg = crm_hygiene_gate(
            lead, conv.get("visitor_email") or "", require_email=False
        )
        if _hyg:
            _record_push("refused", _hyg)
            logger.info(
                f"[CRM-ABANDONED] refused conv={conv.get('id', '?')} reason={_hyg}"
            )
            return CRMResult(success=False, error=f"gate:{_hyg}")

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
        # Silent lead (form filled, zero client messages): the consultant
        # must know exactly what they're dialing BEFORE the call.
        if conv.get("_no_engagement"):
            _tag_line = (
                "No engagement — form only "
                f"(source: {_ab_meta.get('utm_source') or 'direct'}, "
                f"landing: {_ab_meta.get('page_url') or 'unknown'})"
            )
            _existing_ctx = payload.get("chat_context")
            payload["chat_context"] = (
                f"{_tag_line} | {_existing_ctx}" if _existing_ctx else _tag_line
            )
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
            if not req_id:
                # Same proof rule as submit_to_crm: a 200 without an id is
                # the CRM's validation-rejection shape (#1259), not success.
                logger.error(
                    f"[CRM-ABANDONED] FAIL conv={cid} status=200 without id "
                    f"body={resp.text[:300]}"
                )
                return CRMResult(success=False, error="no_id_in_response")
            logger.info(f"[CRM-ABANDONED] OK conv={cid} crm_id={req_id}")
            return CRMResult(success=True, request_id=req_id)

        body = resp.text[:300]
        logger.error(f"[CRM-ABANDONED] FAIL conv={cid} status={resp.status_code} body={body}")
        return CRMResult(success=False, error=f"HTTP {resp.status_code}: {body}")

    except httpx.TimeoutException:
        return CRMResult(success=False, error="Timeout")
    except Exception as e:
        return CRMResult(success=False, error=str(e))
