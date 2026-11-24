"""Create lead in CRM: the agent's button (CRM team's CHAT-LEADS-API v1.1, 24 Nov 2026).

One server-to-server call: POST CRM_CHAT_API_URL (the full /chat-api/v1/leads address) with
`Authorization: Bearer CRM_CHAT_API_KEY`. The key never reaches a browser. The CRM creates the lead with its
own rules (client lookup, the repeat-client owner, merge approvals), gives it to the agent we name, saves the
transcript as a comment and answers with the lead's number and link. `conversation_id` is its idempotency key:
a second call returns the same lead (200, created: false) and changes nothing, so one retry after a timeout or
a 500 can never make a second lead.

What we send, and what we never do:
  - the contact as we have it: an e-mail that looks like one, and a phone only in international form (+…).
    A local number kept for the agent to check (ADR-6f) never leaves: the CRM would read it as US/Canada;
  - the visitor's attribution raw (utm_*, the click ids, the referrer, the page the chat started on). No source
    is inferred here: since v1.1 the CRM applies the same rules as for its own site leads;
  - the trip only from what the client said: IATA codes and a date that has not passed, else no segment;
    passengers only when known. A bad trip never blocks the lead on their side either (TRIP_IGNORED);
  - the transcript oldest first, without the system lines.

Nothing personal is logged: conversation ids, HTTP status and error codes only (backend rule 7). Every
outcome is counted in /health → crm_leads_api (code rule 2).

Switches (app.flags, both off): CRM_LEADS_API turns the button on for everyone; CRM_LEADS_API_PILOT, a
comma-separated list of panel e-mails or user ids, for the agents who try it first.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15.0
MAX_MESSAGES = 2000  # the CRM's limit on the transcript
NOTE_MAX = 2000  # the CRM's limit on `note`

CRM_LEADS_API_HEALTH: dict[str, Any] = {
    "calls": 0,
    "created": 0,
    "existing": 0,
    "retried": 0,
    "timeout": 0,
    "network_error": 0,
    "server_error": 0,
    "not_configured": 0,
    "no_contact": 0,
    "save_failed": 0,
    "mark_failed": 0,
    "status_read_failed": 0,
    "errors": {},
    "warnings": {},
    "last_error_code": None,
    "last_error_at": None,
}

_ROLE_TO_FROM = {"user": "client", "ai": "bot", "agent": "agent"}
_MARKETING_KEYS = (
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "wbraid", "gbraid", "msclkid", "fbclid", "referrer",
)
_CABINS = {
    "economy": "economy",
    "premium_economy": "premium_economy",
    "premium economy": "premium_economy",
    "premium": "premium_economy",
    "business": "business",
    "first": "first",
}
_NAMELESS = {"visitor", "customer", "guest", "client", "chat visitor", "unknown", "anonymous", "n/a", "-"}
_IATA_RE = re.compile(r"^[A-Z]{3}$")
_PLACEHOLDER_IATA = {"AAA", "XXX", "ZZZ"}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")

_client: Optional[httpx.AsyncClient] = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )
    return _client


# ── switches ────────────────────────────────────────────────────


def enabled_for(user: Optional[dict]) -> bool:
    """The button is on for this panel user: everyone (CRM_LEADS_API) or the pilot (CRM_LEADS_API_PILOT)."""
    from app.flags import flag

    if bool(flag("CRM_LEADS_API", False)):
        return True
    pilot = {p.strip().lower() for p in str(flag("CRM_LEADS_API_PILOT", "") or "").split(",") if p.strip()}
    if not pilot or not user:
        return False
    email = str(user.get("email") or "").strip().lower()
    uid = str(user.get("id") or "").strip().lower()
    return bool((email and email in pilot) or (uid and uid in pilot))


def everyone_on() -> bool:
    """The button is on for every agent (not only the pilot)."""
    from app.flags import flag

    return bool(flag("CRM_LEADS_API", False))


def is_configured() -> bool:
    return bool(settings.crm_chat_api_url.strip() and settings.crm_chat_api_key.strip())


# ── what the CRM receives ───────────────────────────────────────


def _int(raw: Any) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw or "").strip()
    return int(text) if text.isdigit() else None


def e164(raw: Any) -> Optional[str]:
    """A phone already in international form, spaces and dashes removed; None for anything else."""
    text = re.sub(r"[\s\-().]", "", str(raw or ""))
    return text if _E164_RE.match(text) else None


def valid_email(raw: Any) -> Optional[str]:
    text = str(raw or "").strip()
    return text if _EMAIL_RE.match(text) else None


def client_of(conv: dict) -> dict:
    """The `client` block: name, e-mail, phone, IP and browser, each only when we really have it."""
    meta = conv.get("metadata") if isinstance(conv.get("metadata"), dict) else {}
    out: dict = {}
    name = str(conv.get("visitor_name") or "").strip()
    if len(name) >= 2 and name.lower() not in _NAMELESS:
        out["name"] = name
    email = valid_email(conv.get("visitor_email"))
    if email:
        out["email"] = email
    phone = e164(conv.get("visitor_phone"))
    if phone:
        out["phone"] = phone
    for ours, theirs in (("client_ip", "ip"), ("user_agent", "user_agent")):
        value = meta.get(ours)
        if isinstance(value, str) and value.strip():
            out[theirs] = value.strip()
    return out


def has_contact(conv: dict) -> bool:
    client = client_of(conv)
    return bool(client.get("email") or client.get("phone"))


def _marketing(meta: dict) -> dict:
    out: dict = {}
    for key in _MARKETING_KEYS:
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    page = meta.get("page_url") or meta.get("landing_page")
    if isinstance(page, str) and page.strip():
        out["landing_page"] = page.strip()
    return out


def _iata(raw: Any) -> Optional[str]:
    code = str(raw or "").strip().upper()
    return code if _IATA_RE.match(code) and code not in _PLACEHOLDER_IATA else None


def _future_day(raw: Any, today: date) -> Optional[str]:
    text = str(raw or "").strip()[:10]
    day: Optional[date] = None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        try:
            day = date.fromisoformat(text)
        except ValueError:  # noqa: silent — a malformed date (2026-02-30) is a date we don't send, not a failure
            day = None
    return day.isoformat() if day and day >= today else None


def trip_of(lead: Optional[dict], today: date) -> dict:
    """The `trip` block from what the lead holds: cabin, passengers when known, and the segments whose airports
    and dates are real (one way or there and back; a multi-city's first leg, the rest is in the transcript)."""
    trip: dict = {}
    if not isinstance(lead, dict):
        return trip
    cabin = _CABINS.get(str(lead.get("cabin_class") or "").strip().lower())
    if cabin:
        trip["cabin_class"] = cabin
    pax = _int(lead.get("passengers"))
    if pax and pax > 0:
        children = _int(lead.get("children_count")) or 0
        infants = _int(lead.get("infant_count")) or 0
        trip["adults"] = max(1, pax - children - infants)
        trip["children"] = children
        trip["infants"] = infants
    origin = _iata(lead.get("origin_code"))
    dest = _iata(lead.get("destination_code"))
    out_day = _future_day(lead.get("departure_date"), today)
    if origin and dest and origin != dest and out_day:
        segments = [{"from": origin, "to": dest, "date": out_day}]
        back_day = _future_day(lead.get("return_date"), today)
        if back_day and back_day >= out_day and lead.get("trip_type") != "one_way":
            segments.append({"from": dest, "to": origin, "date": back_day})
        trip["segments"] = segments
    return trip


def transcript_of(messages: Any) -> list[dict]:
    """The messages, oldest first, client/bot/agent only: the system lines stay out."""
    out: list[dict] = []
    for m in messages if isinstance(messages, list) else []:
        if not isinstance(m, dict):
            continue
        who = _ROLE_TO_FROM.get(str(m.get("role") or ""))
        text = str(m.get("content") or "").strip()
        if not who or not text:
            continue
        item = {"from": who, "text": text}
        if m.get("created_at"):
            item["at"] = str(m["created_at"])
        out.append(item)
    return out[-MAX_MESSAGES:]


def site_of(meta: dict) -> str:
    site = str(meta.get("site") or "").strip().upper()
    return site if site in ("BBC", "BCT") else "BBC"


def build_payload(
    conv: dict,
    *,
    agent_email: Optional[str],
    agent_id: Any = None,
    note: Optional[str] = None,
    today: Optional[date] = None,
) -> dict:
    """The body of POST /chat-api/v1/leads for this conversation (with its `messages` and `lead`)."""
    meta = conv.get("metadata") if isinstance(conv.get("metadata"), dict) else {}
    day = today or datetime.now(timezone.utc).date()
    payload: dict = {
        "conversation_id": str(conv["id"]),
        "site": site_of(meta),
        "chat_url": f"{settings.admin_panel_url.rstrip('/')}/chats?highlight={conv['id']}",
        "client": client_of(conv),
    }
    crm_id = _int(agent_id)
    if crm_id is not None:
        payload["agent_id"] = crm_id
    elif isinstance(agent_id, str) and agent_id.strip():
        payload["agent_id"] = agent_id.strip()
    if agent_email:
        payload["agent_email"] = agent_email.strip()
    text = str(note or "").strip()
    if text:
        payload["note"] = text[:NOTE_MAX]
    marketing = _marketing(meta)
    if marketing:
        payload["marketing"] = marketing
    trip = trip_of(conv.get("lead"), day)
    if trip:
        payload["trip"] = trip
    transcript = transcript_of(conv.get("messages"))
    if transcript:
        payload["transcript"] = transcript
    return payload


# ── the call ────────────────────────────────────────────────────


@dataclass
class CrmLeadCall:
    """What the CRM answered. ok=True only with a lead number (the proof)."""

    ok: bool
    status: int = 0
    data: dict = field(default_factory=dict)
    error_code: str = ""
    message: str = ""
    retry_after: Optional[int] = None


# Written for an admin, not for the agent: what the agent can do is tell someone.
_SETUP_CODES = {"AUTH_INVALID", "IP_NOT_ALLOWED", "NOT_CONFIGURED"}
_SETUP_MESSAGE = "The CRM connection isn't set up right. Tell an admin."


def count_error(code: str) -> None:
    errors = CRM_LEADS_API_HEALTH["errors"]
    errors[code] = int(errors.get(code, 0)) + 1
    CRM_LEADS_API_HEALTH["last_error_code"] = code
    CRM_LEADS_API_HEALTH["last_error_at"] = datetime.now(timezone.utc).isoformat()


def _count_warnings(warnings: Any) -> None:
    counts = CRM_LEADS_API_HEALTH["warnings"]
    for w in warnings if isinstance(warnings, list) else []:
        code = str(w.get("code") or "UNKNOWN") if isinstance(w, dict) else "UNKNOWN"
        counts[code] = int(counts.get(code, 0)) + 1


def _body(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError as e:
        logger.warning(f"[crm-leads-api] HTTP {resp.status_code}: the answer is not JSON ({e})")
        data = None
    return data if isinstance(data, dict) else {}


async def post_lead(payload: dict) -> CrmLeadCall:
    """POST the lead. One retry after a timeout, a network error or a 5xx (safe: same conversation_id)."""
    cid = str(payload.get("conversation_id") or "")
    url = settings.crm_chat_api_url.strip()
    key = settings.crm_chat_api_key.strip()
    if not url or not key:
        CRM_LEADS_API_HEALTH["not_configured"] += 1
        count_error("NOT_CONFIGURED")
        logger.error(f"[crm-leads-api] conv={cid}: CRM_CHAT_API_URL or CRM_CHAT_API_KEY is not set")
        return CrmLeadCall(ok=False, error_code="NOT_CONFIGURED", message=_SETUP_MESSAGE)

    CRM_LEADS_API_HEALTH["calls"] += 1
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    last = CrmLeadCall(ok=False, error_code="NO_ANSWER", message="The CRM didn't answer. Try again.")
    for attempt in (1, 2):
        try:
            resp = await _http().post(url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            CRM_LEADS_API_HEALTH["timeout"] += 1
            logger.warning(f"[crm-leads-api] conv={cid} attempt {attempt}: timeout ({type(e).__name__}: {e})")
            last = CrmLeadCall(ok=False, error_code="TIMEOUT", message="The CRM didn't answer in time. Try again.")
            if attempt == 1:
                CRM_LEADS_API_HEALTH["retried"] += 1
                continue
            break
        except httpx.HTTPError as e:
            CRM_LEADS_API_HEALTH["network_error"] += 1
            logger.warning(f"[crm-leads-api] conv={cid} attempt {attempt}: network error ({type(e).__name__}: {e})")
            last = CrmLeadCall(ok=False, error_code="NETWORK", message="Couldn't reach the CRM. Try again.")
            if attempt == 1:
                CRM_LEADS_API_HEALTH["retried"] += 1
                continue
            break

        body = _body(resp)
        if resp.status_code in (200, 201) and body.get("success") is True and body.get("flight_request_id"):
            created = resp.status_code == 201 and body.get("created") is not False
            CRM_LEADS_API_HEALTH["created" if created else "existing"] += 1
            _count_warnings(body.get("warnings"))
            logger.info(
                f"[crm-leads-api] conv={cid}: HTTP {resp.status_code} lead={body.get('flight_request_id')} "
                f"{'created' if created else 'already there'}"
            )
            return CrmLeadCall(ok=True, status=resp.status_code, data=body)
        if resp.status_code >= 500:
            CRM_LEADS_API_HEALTH["server_error"] += 1
            code = str(body.get("error_code") or "SERVER_ERROR")
            logger.warning(f"[crm-leads-api] conv={cid} attempt {attempt}: HTTP {resp.status_code} {code}")
            last = CrmLeadCall(ok=False, status=resp.status_code, error_code=code,
                               message="The CRM had a problem. Try again in a moment.")
            if attempt == 1:
                CRM_LEADS_API_HEALTH["retried"] += 1
                continue
            break

        # A refusal (4xx), or a 2xx without the lead number: nothing to retry.
        code = str(body.get("error_code") or ("BAD_ANSWER" if resp.status_code < 300 else f"HTTP_{resp.status_code}"))
        message = str(body.get("message") or "").strip() or "The CRM refused the lead."
        if code in _SETUP_CODES:
            message = _SETUP_MESSAGE
        retry_after = _int(resp.headers.get("Retry-After")) if resp.status_code == 429 else None
        count_error(code)
        logger.warning(f"[crm-leads-api] conv={cid}: refused, HTTP {resp.status_code} {code}")
        return CrmLeadCall(ok=False, status=resp.status_code, error_code=code, message=message,
                           retry_after=retry_after)

    count_error(last.error_code)
    logger.error(f"[crm-leads-api] conv={cid}: no lead after the retry ({last.error_code})")
    return last


def record_of(call: CrmLeadCall, *, agent_user_id: Optional[str], by: Optional[str], site: str) -> dict:
    """What we keep on the conversation (metadata.crm_lead): the lead's number and link, who it went to and the
    warnings. The CRM repeats no warning on a second call (v1.1), so they are kept from the first."""
    data = call.data
    warnings = [
        {"code": str(w.get("code") or ""), "message": str(w.get("message") or "")}
        for w in (data.get("warnings") if isinstance(data.get("warnings"), list) else [])
        if isinstance(w, dict)
    ]
    return {
        "flight_request_id": data.get("flight_request_id"),
        "url": data.get("url"),
        "client_id": data.get("client_id"),
        "site": data.get("site") or site,
        "assigned_agent_id": data.get("assigned_agent_id"),
        "assigned_to_requested_agent": data.get("assigned_to_requested_agent"),
        "merge_approval_request_id": data.get("merge_approval_request_id"),
        "warnings": warnings,
        "created": call.status == 201 and data.get("created") is not False,
        "agent_user_id": agent_user_id,
        "by": by,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def stored_record(conv: Optional[dict]) -> Optional[dict]:
    """The lead this chat already made through the button, if any."""
    meta = (conv or {}).get("metadata") if isinstance((conv or {}).get("metadata"), dict) else {}
    rec = meta.get("crm_lead") if isinstance(meta, dict) else None
    return rec if isinstance(rec, dict) and rec.get("flight_request_id") else None
