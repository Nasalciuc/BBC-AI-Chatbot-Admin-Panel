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

The automatic path (owner, 25 Nov 2026, chat #3700): an agent pressed the old "Send to CRM", the lead went to
POST /requests/chatbot, which names no agent, and the CRM gave it by its own rules. Now every old sender (Send to
CRM, the abandoned sweep, the backstop, the bot's own submit) first asks send_for_agent: a chat an agent has had
goes through this API with that agent; a chat no agent had stays on the old endpoint. A refusal for good
(an agent the CRM does not know, a wrong key) sends the old way, so no lead is lost; a call that may have reached
the CRM (a timeout, a 5xx) is not sent the old way, so no lead is made twice: it is tried again here (the CRM keys
the lead on the conversation). CRM_LEADS_API_AUTO=false turns it off; nothing changes until CRM_CHAT_API_URL and
CRM_CHAT_API_KEY are set.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15.0
MAX_MESSAGES = 2000  # the CRM's limit on the transcript
NOTE_MAX = 2000  # the CRM's limit on `note`
# One CRM submit at a time per chat: this button, the old sweep and the backstop each hold the chat's "crm_submit"
# lock from their last read to their receipt, so whichever goes second reads the other's receipt. The Redis key
# outlives a CRM call with its retry (2 x 15 s) and the writes after it.
SUBMIT_LOCK_KIND = "crm_submit"
SUBMIT_LOCK_MS = 60_000

CRM_LEADS_API_HEALTH: dict[str, Any] = {
    "calls": 0,
    "created": 0,
    "existing": 0,
    "retried": 0,
    "timeout": 0,
    "network_error": 0,
    "server_error": 0,
    "not_configured": 0,
    "not_https": 0,
    "busy": 0,
    "no_contact": 0,
    "save_failed": 0,
    "mark_failed": 0,
    "status_read_failed": 0,
    # The automatic path (send_for_agent): sent with the agent, already made, and why the old way was taken.
    "auto_sent": 0,
    "auto_already": 0,
    "auto_retry": 0,
    "auto_fallback": 0,
    "auto_no_agent": 0,
    "auto_no_contact": 0,
    "auto_agent_unread": 0,
    "auto_read_failed": 0,
    "auto_off": 0,
    "auto_not_configured": 0,
    "errors": {},
    "warnings": {},
    "last_error_code": None,
    "last_error_at": None,
    # The CRM's word on our key, asked without making anything (check_key): ok, auth_invalid, ip_not_allowed,
    # no_answer, not_configured or http_<status>; and when.
    "key_check": None,
    "key_check_at": None,
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
_NO_RETURN_LEG = {"oneway", "multicity", "openjaw"}  # trip_type, letters only
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


def _https(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme.lower() == "https" and bool(parts.netloc)


def is_configured() -> bool:
    url = settings.crm_chat_api_url.strip()
    return bool(url and settings.crm_chat_api_key.strip()) and _https(url)


def submit_lock(conversation_id: str):
    """The chat's CRM-submit lock (see SUBMIT_LOCK_KIND). Strict: when another process still holds it after the
    wait, or a configured Redis cannot confirm it, LockBusy is raised and nothing is sent; the caller tries again
    later. Renewed: its key stays alive while the work runs, and `lost` turns true if it stops being ours, which
    every sender checks before a call to the CRM (CodeRabbit #280)."""
    from app.services.conversation_lock import conversation_lock

    return conversation_lock(SUBMIT_LOCK_KIND, conversation_id, ttl_ms=SUBMIT_LOCK_MS, strict=True, renew=True)


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
    and dates are real: one way, or there and back. A multi-city or an open jaw gets its first leg only: its return
    date says nothing about the airports it comes back from, and the rest is in the transcript."""
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
        # Not "only a round trip": a return date with no trip type is one (the extractor reads it that way).
        kind = re.sub(r"[^a-z]", "", str(lead.get("trip_type") or "").lower())
        if back_day and back_day >= out_day and kind not in _NO_RETURN_LEG:
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
_SETUP_CODES = {"AUTH_INVALID", "IP_NOT_ALLOWED", "NOT_CONFIGURED", "NOT_HTTPS"}
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


_LEASE_LOST_MESSAGE = "This chat is being sent to the CRM right now. Try again in a minute."


async def check_key() -> dict:
    """Does the CRM take our key? One POST with an empty body: no conversation, no client, no agent. The CRM refuses
    the body and makes nothing (422: the key held), or refuses the key (401 AUTH_INVALID), or our address (403
    IP_NOT_ALLOWED). The scheduler asks at each start and every 6 hours (crm_chat_api_check); the answer is in /health
    (crm_leads_api.key_check). 25 Nov 2026: a key set in Railway answered 401 on two real leads before anyone knew;
    this says so at the deploy, with no lead at stake. Not configured: nothing is sent."""
    url = settings.crm_chat_api_url.strip()
    key = settings.crm_chat_api_key.strip()
    state = "not_configured"
    if url and key and _https(url):
        try:
            resp = await _http().post(url, json={}, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        except httpx.HTTPError as e:
            logger.warning(f"[crm-leads-api] key check: no answer ({type(e).__name__}: {e})")
            state = "no_answer"
        else:
            code = str(_body(resp).get("error_code") or "")
            if resp.status_code == 401 or code == "AUTH_INVALID":
                state = "auth_invalid"
            elif code == "IP_NOT_ALLOWED":
                state = "ip_not_allowed"
            elif resp.status_code == 422:
                state = "ok"
            else:
                state = f"http_{resp.status_code}"
    CRM_LEADS_API_HEALTH["key_check"] = state
    CRM_LEADS_API_HEALTH["key_check_at"] = datetime.now(timezone.utc).isoformat()
    if state not in ("ok", "not_configured"):
        logger.warning(f"[crm-leads-api] key check: {state}; the leads of agents' chats go the old way, without the agent")
    return {"state": state}


async def post_lead(payload: dict, still_held: Optional[Callable[[], bool]] = None) -> CrmLeadCall:
    """POST the lead. One retry after a timeout, a network error or a 5xx (safe: same conversation_id).

    still_held: asked before each attempt; False (the chat's submit lock is no longer this process's) stops before
    the request leaves (LEASE_LOST), since another process may be sending the same chat now."""
    cid = str(payload.get("conversation_id") or "")
    url = settings.crm_chat_api_url.strip()
    key = settings.crm_chat_api_key.strip()
    if not url or not key:
        CRM_LEADS_API_HEALTH["not_configured"] += 1
        count_error("NOT_CONFIGURED")
        logger.error(f"[crm-leads-api] conv={cid}: CRM_CHAT_API_URL or CRM_CHAT_API_KEY is not set")
        return CrmLeadCall(ok=False, error_code="NOT_CONFIGURED", message=_SETUP_MESSAGE)
    if not _https(url):
        # The key, the contact and the chat never travel in clear: fail closed before a header is built.
        CRM_LEADS_API_HEALTH["not_https"] += 1
        count_error("NOT_HTTPS")
        logger.error(f"[crm-leads-api] conv={cid}: CRM_CHAT_API_URL is not an https address; nothing was sent")
        return CrmLeadCall(ok=False, error_code="NOT_HTTPS", message=_SETUP_MESSAGE)

    CRM_LEADS_API_HEALTH["calls"] += 1
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    last = CrmLeadCall(ok=False, error_code="NO_ANSWER", message="The CRM didn't answer. Try again.")
    for attempt in (1, 2):
        if still_held is not None and not still_held():
            count_error("LEASE_LOST")
            logger.warning(f"[crm-leads-api] conv={cid} attempt {attempt}: the submit lock is no longer ours; not sent")
            return CrmLeadCall(ok=False, error_code="LEASE_LOST", message=_LEASE_LOST_MESSAGE)
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


def is_receipt(rec: Any) -> bool:
    """A kept answer with the lead's number: what metadata.crm_lead holds once the button made the lead."""
    return isinstance(rec, dict) and bool(rec.get("flight_request_id"))


def stored_record(conv: Optional[dict]) -> Optional[dict]:
    """The lead this chat already made through the button, if any."""
    meta = (conv or {}).get("metadata") if isinstance((conv or {}).get("metadata"), dict) else {}
    rec = meta.get("crm_lead") if isinstance(meta, dict) else None
    return rec if is_receipt(rec) else None


# ── the automatic path ──────────────────────────────────────────

AUTO_FLAG = "CRM_LEADS_API_AUTO"
# A call that may have reached the CRM, or one to make again in a moment: never sent the old way (the old endpoint
# would make a second lead). The next send of the chat asks this API again, which answers with the same lead.
_TRY_AGAIN = {"TIMEOUT", "NETWORK", "NO_ANSWER", "BAD_ANSWER", "LEASE_LOST", "SERVER_ERROR", "RATE_LIMITED"}


@dataclass
class AgentSend:
    """What send_for_agent did for one chat.

    route "new": this API handled it. ok=True with the lead's number in request_id (made now or before); ok=False
    with `error` is a failure to try again later, and the caller sends nothing the old way.
    route "old": not this path; `reason` says why (off, not_configured, no_conversation, no_agent, no_contact,
    agent_unread, refused) and the caller sends the old way, as before."""

    route: str
    ok: bool = False
    request_id: str = ""
    error: str = ""
    reason: str = ""


def auto_on() -> bool:
    """The automatic path is on (CRM_LEADS_API_AUTO, on unless set false) and the CRM's address and key are set."""
    from app.flags import flag

    return bool(flag(AUTO_FLAG, True)) and is_configured()


async def send_for_agent(
    conversation_id: str,
    *,
    presser: Optional[dict] = None,
    still_held: Optional[Callable[[], bool]] = None,
) -> AgentSend:
    """Send this chat's lead through this API, to the agent who has had the chat.

    presser: the panel user who pressed Send to CRM, when one did. Their CRM id (crm_sub) is sent only when they
    are that agent, as the button does; the agent's company e-mail always. still_held: the caller's submit lock,
    asked before the request leaves (post_lead)."""
    from app.flags import flag

    if not bool(flag(AUTO_FLAG, True)):
        CRM_LEADS_API_HEALTH["auto_off"] += 1
        return AgentSend(route="old", reason="off")
    if not is_configured():
        CRM_LEADS_API_HEALTH["auto_not_configured"] += 1
        return AgentSend(route="old", reason="not_configured")

    from app.db import supabase as db
    from app.services.lead_access import owner_of_conversation

    cid = str(conversation_id or "").strip()
    conv: Optional[dict] = None
    agent: Optional[dict] = None
    try:
        conv = await db.get_conversation(cid, raise_on_error=True) if cid else None
    except Exception as e:
        # Who had the chat can't be known: nothing is sent now, either way. The next send of the chat asks again.
        CRM_LEADS_API_HEALTH["auto_read_failed"] += 1
        logger.error(f"[crm-leads-api] auto conv={cid}: the chat could not be read ({type(e).__name__}: {e})")
        return AgentSend(route="new", error="crm_api:READ_FAILED", reason="read_failed")
    if not conv:
        # A lead with no chat (an old sender may pass a lead id): nobody to name.
        CRM_LEADS_API_HEALTH["auto_no_agent"] += 1
        return AgentSend(route="old", reason="no_conversation")
    stored = stored_record(conv)
    if stored:
        CRM_LEADS_API_HEALTH["auto_already"] += 1
        return AgentSend(route="new", ok=True, request_id=str(stored["flight_request_id"]), reason="already")
    owner = owner_of_conversation(conv)
    if not owner:
        CRM_LEADS_API_HEALTH["auto_no_agent"] += 1
        return AgentSend(route="old", reason="no_agent")
    if not has_contact(conv):
        # A local number only, say: the old way keeps its own rules for it (ADR-6f).
        CRM_LEADS_API_HEALTH["auto_no_contact"] += 1
        return AgentSend(route="old", reason="no_contact")

    presser = presser or {}
    # None for a missing account and for a failed read alike (db logs the failure): either way no e-mail to name.
    agent = await db.get_user_by_id(owner)
    agent_email = valid_email((agent or {}).get("email"))
    agent_id = presser.get("crm_sub") if presser.get("id") and presser.get("id") == owner else None
    if not agent_email and not agent_id:
        CRM_LEADS_API_HEALTH["auto_agent_unread"] += 1
        logger.error(f"[crm-leads-api] auto conv={cid}: agent {owner} has no readable e-mail; sent the old way")
        return AgentSend(route="old", reason="agent_unread")

    payload = build_payload(conv, agent_email=agent_email, agent_id=agent_id)
    call = await post_lead(payload, still_held=still_held)
    if call.ok:
        record = record_of(call, agent_user_id=owner, by=presser.get("id") or "auto", site=payload["site"])
        saved = await db.patch_conversation_presence(cid, {"crm_lead": record})
        if not saved:
            CRM_LEADS_API_HEALTH["save_failed"] += 1
            logger.error(
                f"[crm-leads-api] auto conv={cid}: lead {record.get('flight_request_id')} made but not kept on the chat"
            )
        CRM_LEADS_API_HEALTH["auto_sent"] += 1
        return AgentSend(route="new", ok=True, request_id=str(record.get("flight_request_id")), reason="sent")
    if call.error_code in _TRY_AGAIN or call.status == 429 or call.status >= 500:
        CRM_LEADS_API_HEALTH["auto_retry"] += 1
        logger.warning(f"[crm-leads-api] auto conv={cid}: {call.error_code}, to try again; nothing sent the old way")
        return AgentSend(route="new", error=f"crm_api:{call.error_code}", reason="retry")
    CRM_LEADS_API_HEALTH["auto_fallback"] += 1
    logger.warning(f"[crm-leads-api] auto conv={cid}: refused ({call.error_code}); sent the old way, without the agent")
    return AgentSend(route="old", error=call.error_code, reason="refused")
