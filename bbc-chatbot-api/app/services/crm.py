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
    # Automatic pushes held back because the same human (phone or email)
    # already reached the CRM from another conversation in the last 30 days.
    "contact_already_in_crm": 0,
    # The "already in the CRM?" read failed, so the automatic push waited a
    # tick instead of guessing. Rising = duplicates were being risked.
    "crm_contact_lookup_failed": 0,
    # Read again under the chat's CRM lock right before an automatic submit, the receipts could not be
    # read: the submit waited a cycle rather than risk a second lead behind the agent's button.
    "receipt_read_failed": 0,
    # Another process held the chat's CRM-submit lock past the wait: this submit waited a cycle.
    "submit_busy": 0,
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


def crm_push_gate(lead: dict, email: str, phone: str = "") -> Optional[str]:
    """Refusal reason for the STRICT (confirm-flow/button) push path:
    hygiene + the score floor. The abandoned/no-engagement path uses
    crm_hygiene_gate alone — BUSINESS RULE (owner, explicit): every
    captured contact is dialable, silent leads included; score never
    blocks a dialable human there. With CRM_SINGLE_CONTACT on, a phone
    alone is enough here too; no contact at all never is."""
    single = single_contact_ok()
    if single and not (email or "").strip() and not format_phone_international(phone or ""):
        return "no_contact"
    reason = crm_hygiene_gate(lead, email, require_email=not single)
    if reason:
        return reason
    if (lead.get("score") or 0) < 40:
        return "low_score"
    return None


def format_phone_international(phone: str, country: Optional[str] = None) -> str:
    """Format phone to E.164: +{country}{number} — digits only after +.

    Handles all known broken formats:
      '1+2108844081'      → '+12108844081'
      '+1+2108844081'     → '+12108844081'
      '+1 (210) 884-4081'  → '+12108844081'
      '2108844081'        → '+12108844081' (assume US)
      '+442079460958'      → '+442079460958'
      '0044207946'        → '+44207946' (strip intl prefix 00)

    `country` (ISO alpha-2, app.services.phone_country.country_hint) places a
    number typed the local way, without its code (#3296, 17 Nov 2026):
      '0412 345 678', 'AU' → '+61412345678' (the trunk 0 goes)
      '02 1234 5678', 'IT' → '+390212345678' (Italy keeps its 0)
      '9876543210', 'IN'   → '+919876543210'
      '912345678', 'PT'    → '+351912345678' (the country's own length, not only 10 digits)
      '351912345678', 'PT' → '+351912345678' (its own code, typed without the +)
    A number with + or 00 keeps its own code. A local number that starts with
    0 and has no country to go with it is NOT guessed: it formats to "", as a
    number too short to dial does — "+1" before it was an impossible number.
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if not digits or len(digits) < 7:
        return ""
    if phone.strip().startswith("+") or digits.startswith("00"):
        return f"+{digits[2:] if digits.startswith('00') else digits}"
    from app.services.phone_country import KEEPS_LEADING_ZERO, NATIONAL_LENGTHS, calling_code

    code = calling_code(country)
    if digits.startswith("0"):
        if code and code != "1" and len(digits) >= 8:
            return f"+{code}{digits if code in KEEPS_LEADING_ZERO else digits.lstrip('0')}"
        return ""
    lengths = NATIONAL_LENGTHS.get(code or "")
    if code and code != "1" and lengths:
        lo, hi = lengths
        own_code = digits.startswith(code) and lo <= len(digits) - len(code) <= hi
        national = lo <= len(digits) <= hi
        if own_code and national:
            # Both readings fit ("4912345678" in Germany: +49 12345678, or Leer's 0491…): not guessed.
            # It formats to "" and is kept for the agent to check (phone_is_ambiguous; CodeRabbit #270).
            return ""
        if own_code:
            return f"+{digits}"
        if national:
            return f"+{code}{digits}"
        return f"+{digits}"
    if len(digits) == 10:
        if code and code != "1":
            return f"+{code}{digits}"
        digits = f"1{digits}"
    return f"+{digits}"


def phone_is_ambiguous(phone: str, country: Optional[str] = None) -> bool:
    """A number the visit's country reads two ways (with its own code typed without the +, or as a national
    number): it is kept for the agent to check, never dialled as a guess."""
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits or raw.startswith("+") or digits.startswith("0"):
        return False
    from app.services.phone_country import NATIONAL_LENGTHS, calling_code

    code = calling_code(country)
    lengths = NATIONAL_LENGTHS.get(code or "")
    if not code or code == "1" or not lengths:
        return False
    lo, hi = lengths
    return digits.startswith(code) and lo <= len(digits) - len(code) <= hi and lo <= len(digits) <= hi


# ── The CRM contract (createAiChat doc is authoritative) ──────
# Every refusal below is a row the CRM would 422 anyway — better refused
# here, visibly, than silently rejected after the flag was written.
_CONTRACT_PHONE_RE = re.compile(r"^\+\d+$")
_IATA_RE = re.compile(r"^[A-Za-z]{3}$")
CRM_CABIN_ENUM = frozenset({"business", "first", "premium_economy"})


def single_contact_ok() -> bool:
    """CRM_SINGLE_CONTACT (app.flags; off): the CRM takes a lead with ONE contact, an
    email or a phone. Off, it needs both — the contract the CRM keeps today (18 Nov
    2026: 422 "The client.email field is required." for a phone-only lead, and all
    351 leads it took in 30 days had both). Turned on only once the CRM's team says
    their side takes one."""
    from app.flags import flag

    return bool(flag("CRM_SINGLE_CONTACT", False))


def validate_crm_payload(payload: dict) -> Optional[str]:
    """Refusal reason, or None when the payload satisfies the contract."""
    client = payload.get("client") or {}
    phone = re.sub(r"[^\d+]", "", str(client.get("phone") or ""))
    email = str(client.get("email") or "").strip()
    single = single_contact_ok()
    if phone or not (single and email):
        if not _CONTRACT_PHONE_RE.match(phone):
            return "phone_format"
    if len((client.get("name") or "").strip()) < 2:
        return "name_too_short"
    if not email and not single:
        # Refused here, visibly (a gate state in the panel), instead of a 422
        # from the CRM every five minutes (#3324, 18 Nov 2026).
        return "email_missing"
    flights = payload.get("flights") or []
    if not flights:
        return "no_flights"
    for leg in flights:
        if not _IATA_RE.match(str(leg.get("from") or "")):
            return "iata_from"
        if not _IATA_RE.match(str(leg.get("to") or "")):
            return "iata_to"
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(leg.get("date") or "")):
            return "date_format"
    adult = (payload.get("passengers") or {}).get("adult")
    if not isinstance(adult, int) or not 1 <= adult <= 9:
        return "adult_range"
    if payload.get("cabin_class") not in CRM_CABIN_ENUM:
        return "cabin_enum"
    return None


def crm_source(meta: dict | None) -> str:
    """Where the client came from, for the CRM's source (owner, 23 Nov: the CRM must see it on every lead): the
    campaign's own tag, as before; else the ad's click id, as the chat's prompt reads it (gclid: google, fbclid: fb,
    msclkid: bing, Kayak's: kayak). On 9-23 Nov 7 chats came with a click id and no tag, and the CRM saw no source.
    "" when neither says anything: a visit PostHog saw come direct (source_via) stays without a campaign source."""
    from app.pipeline.entity_extractor import infer_paid_source

    m = meta if isinstance(meta, dict) else {}
    own = m.get("utm_source")
    if isinstance(own, str) and own.strip():
        return own
    return infer_paid_source(m) or ""


def compute_defaults_notes(lead: dict, conv_metadata: dict | None = None) -> list[str]:
    """What the consultant must know before dialing a defaulted row.

    Every gap the AAA path papers over becomes one honest line — a
    consultant who knows the route is a placeholder opens the call
    differently than one who thinks AAA→AAA is real."""
    meta = conv_metadata or {}
    # MISSING trip facts — these make a row genuinely incomplete.
    gaps: list[str] = []
    if not (lead.get("origin_code") and lead.get("destination_code")):
        gaps.append("route unknown — defaulted to AAA")
    if not lead.get("departure_date"):
        gaps.append("date unknown — defaulted to +30 days")
    if lead.get("trip_type") == "round_trip" and not lead.get("return_date"):
        gaps.append("client said round trip, return unknown")
    # A translated value is NOT a gap: the client told us the cabin, the
    # CRM enum just has no word for it. It must not stamp "incomplete"
    # on an otherwise complete lead.
    extras: list[str] = []
    cabin = str(lead.get("cabin_class") or "").lower().strip()
    if cabin and cabin.replace(" ", "_") not in CRM_CABIN_ENUM:
        extras.append(f"cabin '{cabin}' outside CRM enum — sent as premium_economy")

    if not gaps:
        # A complete lead is NOT "data incomplete" — claiming it would
        # make the consultant distrust the honest notes on the rows that
        # really are thin.
        return extras
    return [
        "form only / data incomplete — "
        f"source: {crm_source(meta) or 'direct'}, "
        f"landing: {meta.get('page_url') or 'unknown'}",
        *gaps,
        *extras,
    ]


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
        # Anchored to the CONVERSATION, never to the clock. Computed from
        # now(), this produced a different date on every push: 17 Aug → 16 Sep,
        # 19 Aug → 18 Sep, 20 Aug → 19 Sep. The consultant then had four
        # records for one client with four departure dates, none of which the
        # client had ever said — and no way to tell which to keep.
        # conversations.metadata has no created_at of its own (that is a
        # COLUMN), so the cron call site injects it as conversation_created_at.
        _anchor = (conv_metadata or {}).get("conversation_created_at")
        _base = None
        if _anchor:
            try:
                _base = datetime.fromisoformat(str(_anchor).replace("Z", "+00:00"))
            except (ValueError, TypeError):  # noqa: silent — unparseable anchor falls back to now(); the log line below says which base was used
                _base = None
        _base = _base or datetime.now(timezone.utc)
        _dep = (_base + timedelta(days=30)).strftime("%Y-%m-%d")
        logger.info(
            f"CRM payload: departure_date defaulted to {_dep} "
            f"(+30d from {'conversation start' if _anchor else 'now — no anchor'})"
        )
    departure = format_date_iso(_dep)
    return_date = lead.get("return_date")

    trip_type = lead.get("trip_type") or ("round_trip" if return_date else "one_way")
    if allow_defaults and trip_type == "round_trip" and not return_date:
        # The CRM would reject a round trip with one leg. Send the truth we
        # have (one_way) — compute_defaults_notes tells the consultant the
        # client DID say round trip and the return is simply unknown.
        trip_type = "one_way"

    cabin = lead.get("cabin_class") or "business"
    cabin_map = {
        "business": "business",
        "first": "first",
        "premium_economy": "premium_economy",
        "premium economy": "premium_economy",
    }
    # Outside the CRM enum (e.g. "economy"): the strict path keeps the
    # historical business default; the defaults path sends the closest
    # legal cabin DOWN, never silently upgrading a client to business.
    cabin_class = cabin_map.get(
        str(cabin).lower(), "premium_economy" if allow_defaults else "business"
    )

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

    from app.services.phone_country import known_country

    # The country chosen with the number places one typed the local way (CodeRabbit #270).
    phone = format_phone_international(getattr(visitor, "phone", "") or "",
                                       known_country(getattr(visitor, "country_code", None)))

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
    _source = crm_source(_meta)
    if _source:
        _utm["_utmsource"] = _source
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
    # Bing's click id, as Google's and Facebook's ride (it was dropped here: a Bing ad chat without its tags reached
    # the CRM with no trace of the ad).
    if _meta.get("msclkid"):
        _utm["msclkid"] = _meta["msclkid"]
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

    _client_name = (getattr(visitor, "name", "") or "").strip()
    if allow_defaults and len(_client_name) < 2:
        # The CRM contract wants ≥2 chars; a one-letter form entry is a
        # dialable human, not a reject — send the same placeholder an
        # empty name gets.
        _client_name = "Customer"

    payload = {
        "trip_type": trip_type,
        "cabin_class": cabin_class,
        "client": {
            "name": _client_name or "Customer",
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
    *,
    source: str = "push",
    set_by: str | None = None,
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

    reason = crm_push_gate(
        lead, getattr(visitor, "email", "") or "", getattr(visitor, "phone", "") or ""
    )
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
        # How it got there (metadata.crm): the system sent it; a person may have pressed Push.
        marked = await db.mark_lead_created_in_crm(
            lead["id"], crm_lead_id=result.request_id, source=source, set_by=set_by
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


def _refused_fields(resp) -> str:
    """The contract fields a CRM 422 names ("client.email"), comma-joined; "unknown" when it names none."""
    try:
        errors = (resp.json() or {}).get("errors") or {}
        fields = sorted(str(k) for k in errors)[:4] if isinstance(errors, dict) else []
    except Exception:  # noqa: silent — a body that is not JSON names no field; the 422 is still a refusal
        fields = []
    return ",".join(fields) or "unknown"


async def submit_abandoned_to_crm(conv: dict, lead: dict | None) -> CRMResult:
    """Submit abandoned conv to CRM. Bypasses check_crm_ready, uses defaults for missing route."""
    if not settings.crm_api_url:
        return CRMResult(success=False, error="CRM URL not configured")

    try:
        lead = lead or {}
        phone = format_phone_international(conv.get("visitor_phone", ""))
        # One contact is enough once the CRM takes it (CRM_SINGLE_CONTACT).
        if not phone and not (single_contact_ok() and (conv.get("visitor_email") or "").strip()):
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
            # metadata plus the conversation's own birth time: the default
            # departure date anchors to it so every re-push computes the SAME
            # date instead of a fresh clock+30d each time.
            conv_metadata={
                **(conv.get("metadata") or {}),
                "conversation_created_at": conv.get("created_at"),
            },
            suid=conv.get("visitor_id"),
            allow_defaults=True,
        )

        _ab_meta = conv.get("metadata") or {}
        cid = conv.get("id", "?")

        # A past departure is NOT dialable as-is (the trip already left):
        # the row goes to the panel's "CRM pending" work-list for a human,
        # never auto-pushed.
        _dep_raw = lead.get("departure_date")
        if _dep_raw:
            from datetime import date as _date

            try:
                if _date.fromisoformat(str(_dep_raw)[:10]) < datetime.now(timezone.utc).date():
                    _record_push("refused", "past_date")
                    logger.info(f"[CRM-ABANDONED] refused conv={cid} reason=past_date")
                    return CRMResult(success=False, error="gate:past_date")
            except ValueError:
                pass  # unparseable date → the contract check below catches it

        # Every gap the AAA defaults paper over becomes an honest line the
        # consultant reads BEFORE dialing. Silent leads lead with that fact.
        _notes = compute_defaults_notes(lead, _ab_meta)
        # A chat the proactive card opened: the route and dates came from the
        # page the client landed on and were never confirmed in chat.
        from app.services.proactive import page_trip_note
        _page_note = page_trip_note(lead)
        if _page_note:
            _notes.append(_page_note)
        if conv.get("_twin_count"):
            _notes.append(
                f"{conv['_twin_count']} duplicate contact conversation(s) — "
                "this is the richest record"
            )
        if conv.get("_no_engagement"):
            # Source/landing appear ONCE: the no-engagement line owns them
            # and replaces the generic header when both would fire.
            _notes = [
                n for n in _notes
                if not n.startswith("form only / data incomplete")
            ]
            _notes.insert(
                0,
                "No engagement — form only "
                f"(source: {_ab_meta.get('utm_source') or 'direct'}, "
                f"landing: {_ab_meta.get('page_url') or 'unknown'})",
            )
        _existing_ctx = payload.get("chat_context")
        if _existing_ctx:
            _notes.append(str(_existing_ctx))
        if _notes:
            payload["chat_context"] = " | ".join(_notes)
        else:
            payload.pop("chat_context", None)

        # The contract (createAiChat doc): refuse what the CRM would 422.
        _contract = validate_crm_payload(payload)
        if _contract:
            _record_push("refused", _contract)
            logger.info(f"[CRM-ABANDONED] refused conv={cid} reason={_contract}")
            return CRMResult(success=False, error=f"gate:{_contract}")

        _ab_site = _ab_meta.get("site")
        _crm_base = _resolve_crm_base(_ab_site)
        endpoint = f"{_crm_base.rstrip('/')}{CRM_CHATBOT_ENDPOINT}"
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

        if resp.status_code == 422:
            # VERBATIM, untruncated: a 422 names the exact contract field
            # the CRM rejected — truncating it is throwing away the fix.
            logger.error(
                f"[CRM-ABANDONED] 422 conv={cid} body(verbatim)={resp.text}"
            )
            # A refusal of THIS data, not an outage: a gate state with the
            # fields it named, so the sweep stops asking again until the data
            # changes (app.api.cron, metadata.crm_refused) and the panel shows why.
            return CRMResult(success=False, error=f"gate:crm_422:{_refused_fields(resp)}")

        body = resp.text[:300]
        logger.error(f"[CRM-ABANDONED] FAIL conv={cid} status={resp.status_code} body={body}")
        return CRMResult(success=False, error=f"HTTP {resp.status_code}: {body}")

    except httpx.TimeoutException:
        return CRMResult(success=False, error="Timeout")
    except Exception as e:
        return CRMResult(success=False, error=str(e))


# ── Reading a lead back from the CRM (off until the CRM has the route) ─────────
# Problem #3 asks "is this id really in the CRM?". The CRM only takes leads today (POST /requests/chatbot);
# reading one by id is a question for its owners (Dan, 21 Nov). The adapter is ready: CRM_LOOKUP_PATH set
# (for example "/requests/{id}") turns it on, without a deploy; until then it answers None, "cannot say",
# and the panel shows how the id got there instead of a verified mark.

CRM_LOOKUP_HEALTH: dict[str, int] = {"found": 0, "not_found": 0, "errors": 0, "off": 0}


async def lookup(crm_lead_id: str, site: str | None = None) -> bool | None:
    """True when the CRM knows this id, False when it says it does not, None when it can't be asked."""
    from app.flags import flag

    path = str(flag("CRM_LOOKUP_PATH", "") or "").strip()
    if not path or not settings.crm_api_url or not crm_lead_id:
        CRM_LOOKUP_HEALTH["off"] += 1
        return None
    import urllib.parse

    url = f"{_resolve_crm_base(site).rstrip('/')}{path.replace('{id}', urllib.parse.quote(crm_lead_id, safe=''))}"
    try:
        resp = await _get_crm_client().get(url)
    except Exception as e:
        CRM_LOOKUP_HEALTH["errors"] += 1
        logger.warning(f"[crm-lookup] {crm_lead_id}: not asked ({type(e).__name__}: {e})")
        return None
    if resp.status_code == 200:
        CRM_LOOKUP_HEALTH["found"] += 1
        return True
    if resp.status_code == 404:
        CRM_LOOKUP_HEALTH["not_found"] += 1
        return False
    CRM_LOOKUP_HEALTH["errors"] += 1
    logger.warning(f"[crm-lookup] {crm_lead_id}: the CRM answered {resp.status_code}")
    return None
