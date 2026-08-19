"""CRM integration — read-only answers to questions the CRM asks us.

Today there is exactly one question: *is this agent present in the chat
panel?* The CRM refuses to let a sales agent work leads unless they are,
because live visitors are routed only to agents whose panel is open and
who pressed Ready — an agent sitting in the CRM with the chat closed
leaves visitors waiting for nobody.

We answer with STATE, never with people: no name, no phone, no team, no
conversation counts. One DB read, zero writes; if this endpoint were
completely wrong it still could not break routing.
"""

import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from config.settings import settings
from app.db import supabase as db
from app.security.crm_token import PURPOSE_PRESENCE, verify_crm_token

logger = logging.getLogger(__name__)

router = APIRouter()

# Aggregate only. WHO was blocked and WHEN is employee surveillance; if
# the CRM wants that record it belongs on their side, where the manager
# who asked for it can be held to it.
# `errors` exists because a gate that is failing is a gate that is OFF:
# every 401/503 makes the CRM fail open, and ops must see that happening
# without reading logs.
PRESENCE_GATE_HEALTH: dict = {"checks": 0, "blocked": 0, "errors": 0}

# A per-check token has no business living for minutes.
PRESENCE_TOKEN_MAX_AGE_SECONDS = 120

# The shared per-IP limiter is tuned for the customer widget
# (rate_daily_max = 300). The CRM's backend is ONE IP asking a question
# per lead-open, so that budget would be gone in minutes and every later
# check would 429 — which the documented contract turns into "do not
# block". A dead gate that looks alive is worse than no gate, so this
# caller gets its own, far larger budget. Still bounded: a leaked token
# cannot be used to hammer the DB.
PRESENCE_RATE_PER_MINUTE = 600
_presence_hits: dict[str, list[float]] = defaultdict(list)
_presence_lock = Lock()

# Only a string that is actually an address may be treated as one: the
# CRM's `sub` is their internal user id, and looking that up would
# resolve every agent to "unknown_user" — a gate that silently never
# blocks anyone.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def _client_ip(request: Optional[Request]) -> str:
    if request is None:
        return "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def check_presence_rate_limit(request: Request = None) -> None:  # type: ignore[assignment]
    """A sliding minute window, sized for a backend caller."""
    ip = _client_ip(request)
    now = time.monotonic()
    with _presence_lock:
        hits = _presence_hits[ip]
        cutoff = now - 60
        hits[:] = [t for t in hits if t > cutoff]
        if len(hits) >= PRESENCE_RATE_PER_MINUTE:
            logger.warning("presence gate: rate limit hit")
            raise HTTPException(429, "Too many presence checks")
        hits.append(now)
        # Bounded memory: forget IPs that stopped asking.
        if len(_presence_hits) > 64:
            for k in [k for k, v in _presence_hits.items() if not v]:
                _presence_hits.pop(k, None)


class AgentPresenceRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=4096)


def _seconds_since(raw) -> Optional[int]:
    """Age of the heartbeat in seconds, or None if it never beat."""
    if not raw:
        return None
    try:
        seen = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    # Clock skew must not read as a negative age.
    return max(0, int((datetime.now(timezone.utc) - seen).total_seconds()))


def evaluate_presence(user: Optional[dict], window_seconds: int) -> dict:
    """The whole decision, pure and testable.

    `is_ready` alone is worthless as presence: nothing ever clears it
    (app/api/agent.py sets it only when the agent presses the button), so
    someone who pressed Ready on Monday and went home is still
    `is_ready=true` today. Only a FRESH pulse makes the flag mean
    anything — hence the window.
    """
    if not user:
        # Never 404: the CRM gets one code path, and an email we don't
        # know is not an agent we may block.
        return {
            "ready": False, "online": False, "exempt": True,
            "reason": "unknown_user", "last_seen_seconds": None,
        }

    role = (user.get("role") or "").strip().lower()
    # 033: an account with no right to chats must never be told to "go into
    # chat" — it is exempt exactly like a non-operator role.
    chat_enabled = bool(user.get("chat_enabled", True))
    wrong_role = role not in db._OPERATOR_ROLES
    exempt = wrong_role or (not chat_enabled)
    active = bool(user.get("is_active", True))
    age = _seconds_since(user.get("last_seen_at"))
    online = age is not None and age <= window_seconds
    # is_ready is NOT part of presence for this gate (spec v2.4 §2/A1): the
    # pulse already says "I am here", and a button left on overnight lies. The
    # real defence against a lying pulse is the response deadline plus the
    # idempotent fallback from #211 — not a button the agent forgets.
    ready = online and not exempt and active

    if exempt:
        # Owner/admin/dev/supervisor — and any role invented after this
        # code was written — are never blocked from working leads. Neither are
        # the accounts management removed from chat entirely: their ROLE is
        # correct, their RIGHT was withdrawn, and "wrong_role" would be a lie
        # the senior reads on their own screen.
        reason = "chat_disabled" if (not wrong_role and not chat_enabled) else "wrong_role"
    elif not active:
        # Deactivation does not reach a live session: the panel keeps
        # heartbeating on an unexpired JWT, so a disabled account can
        # look perfectly present. The login path 403s them; this gate
        # must not answer the opposite about the same person.
        reason = "inactive"
    elif not online:
        reason = "offline"
    else:
        reason = "ok"

    return {
        "ready": ready, "online": online, "exempt": exempt,
        "reason": reason, "last_seen_seconds": age,
    }


async def _resolve_agent(email: str) -> Optional[dict]:
    """Exact match first, then case-insensitive — the users table is not
    guaranteed to store the same casing the CRM sends, and a mismatch
    would resolve every agent to `unknown_user`: a gate that is on, that
    answers, and that never blocks anyone."""
    user = await db.get_user_by_email(email)
    if user:
        return user
    try:
        client = db.get_client()
        res = await db._run_sync(
            lambda: client.table("users").select("*").ilike("email", email).limit(1).execute()
        )
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"presence gate: case-insensitive lookup failed ({type(e).__name__})")
        return None


@router.post("/integration/agent-presence")
async def agent_presence(
    req: AgentPresenceRequest,
    _rate: None = Depends(check_presence_rate_limit),
):
    """Is this agent present in the chat panel? State only.

    The agent's identity comes from the SIGNED TOKEN — never from a query
    string, path segment or header, which land in Railway's request logs
    and would put an employee's email in them. Auth is the token itself:
    this is called by the CRM's backend, not by a panel user.
    """
    try:
        payload = verify_crm_token(
            req.token,
            expected_purpose=PURPOSE_PRESENCE,
            max_age_seconds=PRESENCE_TOKEN_MAX_AGE_SECONDS,
        )
    except HTTPException:
        # A gate that is failing is a gate that is OFF (the CRM fails
        # open on 401/503) — count it so ops can see it.
        PRESENCE_GATE_HEALTH["errors"] += 1
        raise

    raw_email = (payload.get("email") or "").strip().lower()
    if not raw_email:
        # `sub` is the CRM's internal id unless it happens to be an
        # address — never look up an id as if it were an email.
        candidate = str(payload.get("sub") or "").strip().lower()
        raw_email = candidate if _EMAIL_RE.match(candidate) else ""

    user = None
    if raw_email:
        try:
            user = await _resolve_agent(raw_email)
        except Exception as e:  # noqa: BLE001 — a lookup failure must not block anyone
            logger.warning(f"presence gate: user lookup failed ({type(e).__name__})")
            user = None

    result = evaluate_presence(user, settings.crm_presence_window_seconds)
    PRESENCE_GATE_HEALTH["checks"] += 1
    if not result["exempt"] and not result["ready"]:
        PRESENCE_GATE_HEALTH["blocked"] += 1
    return result
