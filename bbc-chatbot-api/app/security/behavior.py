"""Behaviour traps, the chat lock and the frustration lane (PR-15).

Everything here runs in SHADOW first: it counts what it would do and records
who it would have caught. The owner turns each *_ENFORCE on by hand after the
guard report (GET /api/admin/security/trap-report) shows how many real
clients the shadow would have caught — one real client caught and that trap
stays off. Nothing turns itself on after a date.

  BEHAVIOR_TRAPS_ENFORCE    the request-shape traps (middleware) and the
                            operator traps
  CHAT_ATTACK_ENFORCE       the chat lock
  FRUSTRATION_LANE_ENFORCE  the frustration lane (7 days of shadow)

Two traps never wait: the decoy key from the bundle and the bait route in
the widget's code. Nobody legitimate can trip either.

Not traps: OPTIONS, a 401 from an operator whose session expired, operators
on a VPN, a client who writes oddly.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from typing import Optional

from app.flags import flag

logger = logging.getLogger(__name__)

BEHAVIOR_HEALTH: dict = {
    "would_ban": {},       # jail -> count (shadow)
    "banned": {},          # jail -> count (enforced)
    "foreign_origin": 0,
    "chat_lock_would": 0,
    "chat_locked": 0,
    "chat_lock_refused_model": 0,
    "lock_escalated_to_block": 0,
    "frustration_would": 0,
    "frustration_lane": 0,
    "operator_traps": {},
    "upload_mismatch": 0,
}

# Every shadow (and enforced) hit, for the guard report: who would have been
# caught, so the report can look at what they did next.
SHADOW_HITS: "deque[dict]" = deque(maxlen=2000)

WIDGET_BAIT_PATH = "/api/chat/v0/transcript-export"   # named only in the widget bundle


def enforced(kind: str) -> bool:
    return bool(flag(kind, False))


def record(trap: str, *, ip: str = "", conversation_id: str = "", visitor_id: str = "",
           enforced_now: bool = False) -> None:
    bucket = "banned" if enforced_now else "would_ban"
    BEHAVIOR_HEALTH[bucket][trap] = BEHAVIOR_HEALTH[bucket].get(trap, 0) + 1
    SHADOW_HITS.append({
        "trap": trap, "ip": ip, "conversation_id": conversation_id,
        "visitor_id": visitor_id, "at": time.time(), "enforced": enforced_now,
    })


# ── request shape (used by the ASGI guard) ────────────────────

BAD_METHODS = {"TRACE", "TRACK", "CONNECT", "PROPFIND", "PUT"}
SCANNER_UAS = ("sqlmap", "nikto", "nuclei", "zgrab", "masscan", "gobuster", "dirbuster",
               "ffuf", "wfuzz", "feroxbuster", "nmap", "acunetix", "wpscan")
_PROBE_PARAMS = re.compile(r"(?:^|&)(debug|admin|role)=|__proto__", re.I)
_TRAVERSAL = re.compile(r"\.\.|%2e%2e", re.I)


def request_shape_trap(method: str, path: str, raw_path: str, query: str, ua: str) -> Optional[tuple[str, int]]:
    """(trap, ban seconds) for a request no client of ours sends, else None."""
    if method in BAD_METHODS:
        return ("bad_method", 24 * 3600)
    low = (ua or "").lower()
    if any(s in low for s in SCANNER_UAS):
        return ("scanner_ua", 24 * 3600)
    if _PROBE_PARAMS.search(query or ""):
        return ("param_probe", 24 * 3600)
    if _TRAVERSAL.search(raw_path or path or ""):
        return ("traversal", 24 * 3600)
    return None


# ── the chat lock ─────────────────────────────────────────────

# Attack signatures in a message: the request is an attack, not a question.
_ATTACK = [
    re.compile(r"\$\{jndi:", re.I),
    re.compile(r"'\s*or\s*'1'\s*=\s*'1", re.I),
    re.compile(r"\bunion\s+(all\s+)?select\b", re.I),
    re.compile(r"\.\./\.\./"),
    re.compile(r"<script\b", re.I),
    re.compile(r"\{\{\s*7\s*\*\s*7\s*\}\}"),
    re.compile(r"%00"),
    re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"),
]

# Using the bot for something else: its instructions, a role-play, code.
_ABUSE = [
    re.compile(r"ignore\s+(all\s+)?(the\s+)?previous", re.I),
    re.compile(r"(new|override|change)\s+(your\s+)?instructions?", re.I),
    re.compile(r"(system|original)\s+prompt", re.I),
    re.compile(r"repeat\s+(the\s+)?(text|words|everything)\s+above", re.I),
    re.compile(r"what\s+(are|were)\s+your\s+(instructions|rules|guidelines)", re.I),
    # Role-play aimed at the model itself ("act as my agent" is a client
    # talking about travel and must pass — review fix).
    re.compile(r"\b(act|behave|pretend)\s+(as|like|to\s+be)\s+(a|an)\s+(\w+\s+)?"
               r"(ai|gpt|chatgpt|llm|chatbot|assistant\s+without|unrestricted|unfiltered|linux|terminal)\b", re.I),
    re.compile(r"\bdeveloper\s+mode\b", re.I),
    re.compile(r"\bjail\s*break", re.I),
    # Code requests need a writing verb and something code-shaped: "give me
    # the code" (a promo or booking code) must pass.
    re.compile(r"\b(write|generate|code)\s+(me\s+)?(a\s+|an\s+|some\s+|the\s+)?"
               r"(python|javascript|typescript|java|c\+\+|c#|sql|bash|html|css|rust|go|php)\s*"
               r"(code|script|program|function|class|query)?\b", re.I),
    re.compile(r"\b(write|generate)\s+(me\s+)?(a\s+|some\s+|the\s+)?(code|script|program|function)\s+"
               r"(that|which|to|for)\b", re.I),
    re.compile(r"\bhello\s+world\b.*\b(code|program|in\s+(python|java|javascript|c|c\+\+|rust|go))\b", re.I),
    re.compile(r"\b(write|give)\s+(me\s+)?(a\s+)?hello\s+world\b", re.I),
    re.compile(r"\b(do|solve|write)\s+my\s+(homework|essay|assignment)\b", re.I),
]


def chat_attack(raw_message: str) -> Optional[str]:
    """'attack_signature' | 'abuse' | None. Runs on the RAW message (the
    sanitizer strips tags, which would hide <script)."""
    text = raw_message or ""
    if any(p.search(text) for p in _ATTACK):
        return "attack_signature"
    if any(p.search(text) for p in _ABUSE):
        return "abuse"
    return None


LOCK_MESSAGE = "This chat is for flight bookings. For anything else, please call {contact_phone}."


def lock_message(site: Optional[str]) -> str:
    from app.ai.prompts import get_brand_vars
    return LOCK_MESSAGE.format(contact_phone=get_brand_vars(site).get("contact_phone", ""))


def is_locked(metadata: Optional[dict]) -> bool:
    return (metadata or {}).get("locked") == "abuse"


# ── the frustration lane ──────────────────────────────────────

_FRUSTRATION = re.compile(
    # "connecting flight for 1 person" must not match (review fix): the
    # verb, a preposition, then who.
    r"\b(talk|speak|chat)\s+(to|with)\s+(a\s+|an\s+|the\s+|your\s+)?(real\s+|live\s+)?"
    r"(agent|human|person|someone|consultant|representative)\b"
    r"|\breal\s+(person|human)\b"
    r"|\bi\s+(just\s+)?need\s+a\s+quote\b"
    r"|why\s+am\s+i\s+repeating"
    r"|waste\s+of\s+(my\s+)?time"
    r"|\bjust\s+call\s+me\b",
    re.I,
)


# Short replies that ANSWER the bot ("2", "yes", "ok", "no", "LHR") are not
# frustration (review fix): only short replies that answer nothing count.
_ANSWER_LIKE = re.compile(r"^(\d+|y|n|yes|yep|yup|no|nope|ok|okay|k|sure|si|da|nu|[a-z]{3})[.!]?$", re.I)


def _short_answer(message: str) -> bool:
    m = (message or "").strip()
    return 0 < len(m) <= 3 and not _ANSWER_LIKE.match(m)


def frustration_signal(message: str, recent_user_messages: list[str]) -> bool:
    if _FRUSTRATION.search(message or ""):
        return True
    # Three short non-answers in a row (this one included): "?", "..", "??".
    last = [m for m in (recent_user_messages or [])][-2:] + [message or ""]
    return len(last) == 3 and all(_short_answer(m) for m in last)


def frustration_message(lead: Optional[dict], site: Optional[str]) -> str:
    """Built from the saved lead — no model call."""
    from app.ai.prompts import get_brand_vars

    lead = lead or {}
    o, d = lead.get("origin_code"), lead.get("destination_code")
    route = f"{o} → {d}" if o and d else (lead.get("route_display") or "your trip")
    dates = _fmt_day(lead.get("departure_date"))
    if dates and lead.get("return_date"):
        dates = f"{dates} – {_fmt_day(lead.get('return_date'))}"
    pax = lead.get("passengers") or lead.get("adults")
    travelers = f"{pax} traveler{'s' if pax and int(pax) != 1 else ''}" if pax else "travelers to confirm"
    phone = get_brand_vars(site).get("contact_phone", "")
    return (
        f"Got it — let's get you a real quote. I have: {route} · {dates or 'dates to confirm'} · {travelers}. "
        f"A consultant will call you at the number you left shortly. For right now: {phone}."
    )


def _fmt_day(v) -> str:
    if not v:
        return ""
    from datetime import date, datetime
    try:
        d = v if isinstance(v, date) else datetime.fromisoformat(str(v)[:10]).date()
    except ValueError:  # noqa: silent — an unparseable date is shown as missing, never as a crash
        return ""
    return f"{d.strftime('%b')} {d.day}"


# ── uploads ───────────────────────────────────────────────────

_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}


def upload_matches(content_type: str, data: bytes) -> bool:
    """The first bytes say what the declared type says, and the file is at
    least 1 KB (a lied-about type, or a probe, is caught here)."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if len(data or b"") < 1024:
        return False
    if ct == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return any(data.startswith(m) for m in _MAGIC.get(ct, ()))


def _reset_for_tests() -> None:
    BEHAVIOR_HEALTH.update(would_ban={}, banned={}, foreign_origin=0, chat_lock_would=0,
                           chat_locked=0, chat_lock_refused_model=0, lock_escalated_to_block=0,
                           frustration_would=0, frustration_lane=0, operator_traps={},
                           upload_mismatch=0)
    SHADOW_HITS.clear()


# ── the chat door (called from POST /chat) ────────────────────

async def _ban_ip(ip: str, trap: str, seconds: int) -> None:
    from app.security.ban_store import get_store
    store = get_store()
    if store is not None and ip:
        await store.ban(ip, trap, seconds)


async def _lock(conversation_id: str, reason: str) -> dict:
    """Write metadata.locked AFTER re-reading the fresh metadata, so a key
    another request wrote a moment ago is not overwritten."""
    from datetime import datetime, timezone

    from app.db import supabase as db

    conv = await db.get_conversation_simple(conversation_id) or {}
    meta = dict(conv.get("metadata") or {})
    meta.update(locked="abuse", locked_reason=reason,
                locked_at=datetime.now(timezone.utc).isoformat())
    await db.update_conversation(conversation_id, {"metadata": meta})
    conv["metadata"] = meta
    return conv


_UNSET = object()


async def chat_door(*, conversation_id: Optional[str], raw_message: str, clean_message: str,
                    ip: str, visitor_id: Optional[str], site: Optional[str],
                    metadata=_UNSET, human_handled: bool = False) -> Optional[str]:
    """Before the pipeline, after the routing checks. Returns the reply to
    send instead of calling the model, or None to carry on.

    `metadata` is the conversation's metadata the caller already read (no
    second round-trip on every message); the lock itself re-reads fresh.
    Saves the client's message first on every path that answers (rule 4)."""
    if not conversation_id:
        return None
    from app.db import supabase as db
    from app.services import conversation_service as cs

    if metadata is _UNSET:
        conv = await db.get_conversation_simple(conversation_id) or {}
        metadata = conv.get("metadata")
    meta = dict(metadata or {})
    lock_on = enforced("CHAT_ATTACK_ENFORCE")

    if lock_on and is_locked(meta):
        await cs.add_message(conversation_id, "user", clean_message)
        reply = lock_message(site)
        await cs.add_message(conversation_id, "ai", reply, model_used="template", cost=0.0)
        BEHAVIOR_HEALTH["chat_lock_refused_model"] += 1
        return reply

    kind = chat_attack(raw_message)
    if kind and human_handled:
        # An operator is in the chat: they decide. Counted, never locked.
        record("chat_lock_human_mode", ip=ip, conversation_id=conversation_id, visitor_id=visitor_id or "")
        return None
    if kind:
        record("chat_lock", ip=ip, conversation_id=conversation_id, visitor_id=visitor_id or "",
               enforced_now=lock_on)
        if kind == "attack_signature":
            traps_on = enforced("BEHAVIOR_TRAPS_ENFORCE")
            record("attack_signature", ip=ip, conversation_id=conversation_id,
                   visitor_id=visitor_id or "", enforced_now=traps_on)
            if traps_on:
                await _ban_ip(ip, "attack_signature", 24 * 3600)
        if not lock_on:
            BEHAVIOR_HEALTH["chat_lock_would"] += 1
            return None
        await cs.add_message(conversation_id, "user", clean_message)
        locked = await _lock(conversation_id, kind)
        BEHAVIOR_HEALTH["chat_locked"] += 1
        reply = lock_message(site)
        await cs.add_message(conversation_id, "ai", reply, model_used="template", cost=0.0)
        await _maybe_block(locked, visitor_id, ip)
        return reply
    return None


async def _maybe_block(conv: dict, visitor_id: Optional[str], ip: str) -> None:
    """Two locks in 24 h on the same identity → the existing blocklist."""
    from app.security.ban_store import get_store

    store = get_store()
    ident = visitor_id or ip
    if store is None or not ident:
        return
    n = await store.strike(f"lock:{ident}", 24 * 3600)
    if n >= 2:
        from app.services.blocklist import block_from_conversation
        await block_from_conversation(conv, reason="chat_lock_repeat")
        BEHAVIOR_HEALTH["lock_escalated_to_block"] += 1


def _human_handled(conv: Optional[dict]) -> bool:
    """An operator has the conversation: the bot's lanes stand aside."""
    c = conv or {}
    return c.get("mode") == "human" or bool(c.get("assigned_agent_id"))


async def frustration_door(*, conversation_id: Optional[str], message: str,
                           site: Optional[str], conv: Optional[dict] = None) -> Optional[str]:
    """The frustration lane. Shadow (FRUSTRATION_LANE_ENFORCE off, 7 days):
    counted only. On: no model — a reply built from the saved lead, the
    conversation goes to needs_agent and the lead to the CRM now.

    Never in a conversation an operator handles (review fix: it would post a
    bot template into the operator's chat)."""
    if not conversation_id or _human_handled(conv):
        return None
    from app.db import supabase as db

    # The history is read only when the short-answers rule could apply; the
    # phrase rule needs none (no extra round-trip on an ordinary message).
    if frustration_signal(message, []):
        pass
    elif _short_answer(message):
        recent = await db.get_recent_messages(conversation_id, limit=6)
        users = [m.get("content") or "" for m in (recent or []) if m.get("role") == "user"]
        if not frustration_signal(message, users[-2:]):
            return None
    else:
        return None
    if not enforced("FRUSTRATION_LANE_ENFORCE"):
        BEHAVIOR_HEALTH["frustration_would"] += 1
        record("frustration", conversation_id=conversation_id)
        return None

    from app.services import conversation_service as cs
    from app.services.lead_service import get_or_create_lead

    await cs.add_message(conversation_id, "user", message)
    lead = await get_or_create_lead(conversation_id)
    reply = frustration_message(lead, site)
    await cs.add_message(conversation_id, "ai", reply, model_used="template", cost=0.0)
    await db.update_conversation(conversation_id, {"status": "needs_agent"})
    BEHAVIOR_HEALTH["frustration_lane"] += 1
    if lead and not lead.get("created_in_crm"):
        try:
            from types import SimpleNamespace
            from app.services.crm import push_lead_to_crm

            from app.security.form_trap import holds_crm

            conv = await db.get_conversation_simple(conversation_id) or {}
            if not is_locked(conv.get("metadata")) and not holds_crm(conv.get("metadata")):
                await push_lead_to_crm(
                    lead,
                    SimpleNamespace(name=conv.get("visitor_name") or "", email=conv.get("visitor_email") or "",
                                    phone=conv.get("visitor_phone") or ""),
                    conversation_id,
                    conv_metadata=conv.get("metadata"),
                    suid=conv.get("visitor_id"),
                )
        except Exception as e:
            logger.warning(f"[frustration] CRM push failed: {type(e).__name__}: {e}")
    return reply


# ── the guard report ──────────────────────────────────────────

async def _lead_has_trip(conversation_ids: list[str]) -> set[str]:
    """Of these conversations, the ones whose lead got a route or a date."""
    if not conversation_ids:
        return set()
    from app.db.supabase import _run_sync, get_client

    res = await _run_sync(
        lambda: get_client().table("leads")
        .select("conversation_id,origin_code,destination_code,departure_date")
        .in_("conversation_id", conversation_ids[:200]).execute(),
        label="trap_report",
    )
    return {
        r["conversation_id"] for r in (getattr(res, "data", None) or [])
        if (r.get("origin_code") and r.get("destination_code")) or r.get("departure_date")
    }


async def _ip_went_on_normally(ip: str, since_iso: str) -> bool:
    """A conversation from this IP, after the hit, that the client kept
    writing in or that got a route or a date."""
    from app.db.supabase import _run_sync, get_client

    res = await _run_sync(
        lambda: get_client().table("conversations")
        .select("id,last_user_message_at")
        .eq("metadata->>client_ip", ip).gte("created_at", since_iso).limit(20).execute(),
        label="trap_report",
    )
    rows = getattr(res, "data", None) or []
    if any((r.get("last_user_message_at") or "") > since_iso for r in rows):
        return True
    return bool(await _lead_has_trip([r["id"] for r in rows if r.get("id")]))


async def build_trap_report() -> dict:
    """Per trap: how many hits, how many identities, and how many of them
    were real clients — a locked conversation that then got a route or a
    date, or an IP that then wrote normally. One real client and the trap's
    verdict is "keep off"."""
    from datetime import datetime, timezone

    by_trap: dict[str, dict] = {}
    hits = list(SHADOW_HITS)
    conv_ids = sorted({h["conversation_id"] for h in hits if h.get("conversation_id")})
    try:
        with_trip = await _lead_has_trip(conv_ids)
    except Exception as e:
        logger.warning(f"[trap-report] lead lookup failed: {type(e).__name__}: {e}")
        with_trip = set()
    checked_ips: dict[tuple[str, str], bool] = {}
    for h in hits:
        t = by_trap.setdefault(h["trap"], {"hits": 0, "identities": set(), "real_clients": set()})
        t["hits"] += 1
        ident = h.get("conversation_id") or h.get("visitor_id") or h.get("ip") or "?"
        t["identities"].add(ident)
        if h.get("conversation_id") in with_trip:
            t["real_clients"].add(ident)
        elif h.get("ip") and not h.get("conversation_id"):
            since = datetime.fromtimestamp(h["at"], tz=timezone.utc).isoformat()
            key = (h["ip"], since[:16])
            if key not in checked_ips:
                try:
                    checked_ips[key] = await _ip_went_on_normally(h["ip"], since)
                except Exception as e:
                    logger.warning(f"[trap-report] ip lookup failed: {type(e).__name__}: {e}")
                    checked_ips[key] = False
            if checked_ips[key]:
                t["real_clients"].add(ident)
    out = {}
    for trap, t in sorted(by_trap.items()):
        real = len(t["real_clients"])
        out[trap] = {
            "hits": t["hits"],
            "identities": len(t["identities"]),
            "real_clients_caught": real,
            "verdict": "keep off" if real else "can be turned on",
        }
    return out
