"""Closing message utilities — single source of truth for brand text + atomic claim."""

import logging
from datetime import datetime, timezone

from app.ai.prompts import get_brand_vars
from app.db import supabase as db
from config.settings import settings

logger = logging.getLogger(__name__)


def compute_closing_text(site: str | None = None) -> str:
    """Resolve closing message for the correct brand (BBC vs BCT).
    Always use this — never reference settings.post_crm_closing_message directly.

    Since the warmth restoration this is the FALLBACK, not the default:
    a static brand line is what a client gets when generation fails or
    runs out of time, never what they get for saying YES."""
    brand = get_brand_vars(site)
    return brand.get("closing_message") or settings.post_crm_closing_message


# ── The last thing a paying client reads ──────────────────────
# #188 took confirmation away from the LLM (correctly — the state machine
# owns YES) and accidentally took the CLOSING with it: every confirmation
# since has ended on the same static brand line (the Donna transcript).
# The state stays deterministic; the WORDS come back to life.

TEAM_HOURS = (9, 18)  # 9 AM – 6 PM, US Eastern (matches the after_hours template)
CLOSING_FIRST_CHUNK_BUDGET = 6.0   # seconds — past this, the client waits too long
CLOSING_TOTAL_BUDGET = 15.0


def is_within_team_hours(now: datetime | None = None) -> bool:
    """Are consultants at their desks right now? Drives the PROMISE, so it
    must never crash: python:3.11-slim ships without tzdata, so a missing
    zoneinfo database degrades to a fixed EST offset instead of raising."""
    now = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        local = now.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001 — tzdata absent on slim images
        from datetime import timedelta

        local = now.astimezone(timezone.utc) - timedelta(hours=5)
    if local.weekday() >= 5:  # Saturday/Sunday
        return False
    return TEAM_HOURS[0] <= local.hour < TEAM_HOURS[1]


def closing_promise(crm_ok: bool, now: datetime | None = None) -> str:
    """What we are allowed to promise — nothing more.

    A lead that did NOT reach the CRM must never carry a 30-minute
    promise: it is sitting in the work-list waiting for a human to notice
    it, and a broken promise costs more than a vaguer one."""
    if not crm_ok:
        return "your consultant will pick this up personally"
    if is_within_team_hours(now):
        return "your consultant typically reaches out within ~30 minutes"
    return "your consultant will reach out first thing when the team comes online"


def _trip_context(lead: dict, visitor) -> str:
    """Only what we actually know — a thin lead must not be dressed up."""
    bits = []
    name = (getattr(visitor, "name", "") or "").strip()
    if name:
        bits.append(f"Client name: {name}")
    route = " → ".join(
        p for p in (lead.get("origin_code"), lead.get("destination_code")) if p
    )
    if route:
        bits.append(f"Route: {route}")
    if lead.get("departure_date"):
        bits.append(f"Departure: {lead['departure_date']}")
    if lead.get("return_date"):
        bits.append(f"Return: {lead['return_date']}")
    if lead.get("passengers"):
        bits.append(f"Travelers: {lead['passengers']}")
    if lead.get("cabin_class"):
        bits.append(f"Cabin: {lead['cabin_class']}")
    signals = lead.get("intent_signals")
    if isinstance(signals, dict):
        if signals.get("occasion"):
            bits.append(f"Occasion: {signals['occasion']}")
        if signals.get("must_haves"):
            bits.append(f"They asked for: {signals['must_haves']}")
    return "\n".join(bits) or "No trip details captured."


def is_thin_lead(lead: dict) -> bool:
    """A lead with no real route or date — the AAA class. Nothing specific
    to reference, so the closing must NOT pretend there is."""
    origin = (lead.get("origin_code") or "").upper()
    dest = (lead.get("destination_code") or "").upper()
    has_route = bool(origin and dest and origin != "AAA" and dest != "AAA")
    return not (has_route and lead.get("departure_date"))


def build_closing_prompt(
    lead: dict, visitor, promise: str, phone: str
) -> tuple[str, str]:
    """(system, user) for the closing turn. Deliberately narrow: this model
    call has ONE job and a long list of things it may not do."""
    thin = is_thin_lead(lead)
    detail_rule = (
        "Do NOT invent or imply any trip detail — you have none. Acknowledge "
        "warmly and say their consultant will confirm the details directly."
        if thin else
        "MANDATORY: reference one CONCRETE detail of THEIR trip (their route, "
        "their dates, their occasion) in your own words. A closing that would "
        "fit any other client is a failure."
    )
    system = (
        "You are Mason, a senior private-travel consultant's voice at the very "
        "end of a chat. The client just confirmed their trip summary. Write "
        "their closing message.\n\n"
        f"{detail_rule}\n\n"
        "RULES — all of them are hard:\n"
        f"- The promise is EXACTLY this and nothing stronger: {promise}.\n"
        "- Ask NO questions. The conversation is finished.\n"
        "- Never mention connections, hubs, airlines, aircraft, flight "
        "durations, seat maps or prices — you do not have live inventory and "
        "inventing it is a lie the consultant has to walk back.\n"
        f"- The phone number ({phone}) is a SAFETY NET only: at most a short "
        "closing offer, never the main path.\n"
        "- 2-3 sentences. Warm, calm, human. No emoji, no exclamation storm, "
        "no 'Great choice!' filler, no restating the whole summary back.\n"
        "- Plain text only."
    )
    user = (
        "Their confirmed trip:\n"
        f"{_trip_context(lead, visitor)}\n\n"
        "Write the closing message now."
    )
    return system, user


async def generate_closing(
    lead: dict,
    visitor,
    *,
    crm_ok: bool,
    site: str | None = None,
    on_chunk=None,
) -> tuple[str | None, str, float]:
    """Stream the closing on the premium tier. Returns (text|None, model, cost).

    None means "use compute_closing_text" — the client waited long enough.
    A first chunk that has not arrived within CLOSING_FIRST_CHUNK_BUDGET is
    abandoned: warmth is worth six seconds, not sixteen."""
    import asyncio

    from app.ai.claude import stream_opus

    brand = get_brand_vars(site)
    phone = brand.get("contact_phone") or ""
    promise = closing_promise(crm_ok)
    system, user = build_closing_prompt(lead, visitor, promise, phone)

    loop = asyncio.get_running_loop()
    first_chunk = asyncio.Event()

    def _chunk(text: str):
        if not first_chunk.is_set():
            loop.call_soon_threadsafe(first_chunk.set)
        if on_chunk:
            on_chunk(text)

    task = asyncio.create_task(
        asyncio.to_thread(stream_opus, system, user, _chunk)
    )
    try:
        await asyncio.wait_for(first_chunk.wait(), timeout=CLOSING_FIRST_CHUNK_BUDGET)
        text, cost = await asyncio.wait_for(task, timeout=CLOSING_TOTAL_BUDGET)
    except asyncio.TimeoutError:
        task.cancel()
        logger.warning("Closing generation too slow — brand template served")
        return None, "template", 0.0
    except Exception as e:  # noqa: BLE001 — the client must still be closed
        logger.error(f"Closing generation failed: {e}")
        return None, "template", 0.0

    if not text or not text.strip():
        logger.warning("Closing generation returned empty — brand template served")
        return None, "template", 0.0
    return text.strip(), settings.claude_opus_model, cost


async def claim_closing_sent(conversation_id: str) -> bool:
    """Claim the right to send closing message. Returns True ONLY for the
    first caller. Second+ callers get False (closing already sent).

    Not fully atomic (read-then-write via Supabase client), but at ~2 conv/hour
    the race window is <50ms. Logs ERROR on failure per learning:
    'silent swallow on critical paths converts bugs into multi-day mysteries.'
    """
    try:
        conv = await db.get_conversation_simple(conversation_id)
        meta = dict((conv or {}).get("metadata") or {})
        if meta.get("closing_sent_at"):
            logger.info(f"[{conversation_id}] closing_sent_at already set — skip duplicate")
            return False
        meta["closing_sent_at"] = datetime.now(timezone.utc).isoformat()
        await db.update_conversation(conversation_id, {"metadata": meta})
        logger.info(f"[{conversation_id}] closing_sent_at claimed — sending closing")
        return True
    except Exception as e:
        logger.error(f"[{conversation_id}] claim_closing_sent FAILED: {e}")
        return False


async def claim_super_alert(conversation_id: str, cooldown_minutes: int) -> bool:
    """Returns True if this caller should send the super alert (claims it).
    Read-then-write like claim_closing_sent — accepts ~50ms race window.
    Prevents repeated alerts within cooldown for the same conversation."""
    try:
        conv = await db.get_conversation_simple(conversation_id)
        if not conv:
            return False
        meta = dict((conv or {}).get("metadata") or {})
        last = meta.get("super_notified_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(
                    last.replace("Z", "+00:00") if isinstance(last, str) else last
                )
                if (datetime.now(timezone.utc) - last_dt).total_seconds() < cooldown_minutes * 60:
                    logger.info(f"[{conversation_id}] super_notified_at within cooldown — skip")
                    return False
            except (ValueError, TypeError):
                pass
        meta["super_notified_at"] = datetime.now(timezone.utc).isoformat()
        await db.update_conversation(conversation_id, {"metadata": meta})
        logger.info(f"[{conversation_id}] super_notified_at claimed — sending alert")
        return True
    except Exception as e:
        logger.error(f"[{conversation_id}] claim_super_alert FAILED: {e}")
        return False


def has_closing_been_sent(metadata: dict | None) -> bool:
    """Check if closing was already sent (for guards without DB call)."""
    return bool((metadata or {}).get("closing_sent_at"))
