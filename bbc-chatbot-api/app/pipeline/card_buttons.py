"""The buttons after the card, answered without the model (18 Nov 2026, "12 probleme Kayak și chat").

The widget sends each tap as the client's message with `button` beside it (ChatRequest.button):

- ``yes``: the card's yes, on a card that said the whole trip back (proactive.full_trip). Answered at
  once with the trip said back, what happens next and where to write (card_words.confirm, A2/ADR-10a):
  the model came in at 6.4 s with "best email to start?", and 7 of the 25 card chats ended there.
  Only the conversation's first answer: a yes typed later is the model's.
- ``whatsapp`` / ``email``: the contact the client left on the channel they picked (the owner, 18 Nov:
  the client says where the agent writes — no business number to message). It lands where the CRM,
  the panel and the agent read it (the conversation row, through lead_service), with the channel on the
  lead, and the client is told when the agent writes (the Team line's promise). No second contact is
  asked for (ADR-5, ADR-8). A message with no number or no email in it goes to the model as before.
- ``wait``: "I'll wait here" (only offered while the team is at work, ADR-5b): the conversation joins the
  line for a person, and "about a minute" is promised only while an agent is online (B2, ADR-6e).

before_you_go (B1, ADR-6d) is the one message for a client with no contact who is leaving, asked for by
the widget (tab hidden, idle, chat closed) and checked here: no contact on the row, no agent's word yet,
said once per conversation.

The prompt never sees these turns' replies as its own words to change: they are templates, stored as
such, and the output guard still reads them on the way in (db.add_message).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from app.models.chat import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

BUTTONS: tuple[str, ...] = ("yes", "whatsapp", "email", "wait")
MODEL_USED = "template"

CARD_BUTTONS_HEALTH: dict = {
    "yes": 0, "whatsapp": 0, "email": 0, "wait": 0, "fell_through": 0, "save_failures": 0,
    "contact_not_saved": 0, "before_you_go": 0, "before_you_go_skipped": 0, "before_you_go_too_long": 0,
    "goodbye_asked": 0, "goodbye_no_contact": 0,
}

_PRICE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k)?\b", re.I)


def price_from(raw: Optional[str]) -> Optional[str]:
    """The price the client typed in the optional field, as the lead keeps prices ("$4,200"), or None."""
    if not raw:
        return None
    m = _PRICE_RE.search(str(raw)[:40])
    if not m:
        return None
    from app.pipeline.entity_extractor import _normalize_amount

    return _normalize_amount(m.group(1), m.group(2))


def _trip(meta: dict):
    from app.models.trip_context import TripContext

    raw = meta.get("proactive_trip")
    if not isinstance(raw, dict):
        return None
    try:
        return TripContext(**raw)
    except Exception as e:  # noqa: silent — logged; no trip means the model answers, as before
        logger.warning(f"[card-buttons] stored trip unreadable ({type(e).__name__})")
        return None


def _lang(meta: dict) -> Optional[str]:
    from app.services.proactive_i18n import card_lang

    return card_lang(meta.get("client_lang"))


def _team_online() -> bool:
    try:
        from app.services.closing import is_within_team_hours

        return is_within_team_hours()
    except Exception as e:  # noqa: silent — logged; unknown hours promise the vaguer time
        logger.warning(f"[card-buttons] team hours unknown ({type(e).__name__}): the vaguer promise")
        return False


async def _save(cid: str, role: str, text: str, model_used: Optional[str] = None) -> Optional[dict]:
    from app.services import conversation_service

    row = await conversation_service.add_message(
        cid, role, text, **({"model_used": model_used, "cost": 0.0} if model_used else {}),
    )
    if not isinstance(row, dict):
        CARD_BUTTONS_HEALTH["save_failures"] += 1
        logger.error(f"[{cid}] card button: the {role} message was not saved")
        return None
    return row


def _response(cid: str, row: Optional[dict], text: str, buttons: list[dict], model_used: str) -> ChatResponse:
    content = row.get("content") if isinstance(row, dict) and isinstance(row.get("content"), str) else text
    return ChatResponse(
        conversation_id=cid, message=content, type="template", model_used=model_used, cost=0.0,
        quick_replies=[b["label"] for b in buttons] or None, buttons=buttons or None,
        message_id=(row or {}).get("id"), created_at=(row or {}).get("created_at"),
    )


async def answer(req: ChatRequest, message: str, request_meta: Optional[dict] = None) -> Optional[ChatResponse]:
    """The reply to a tapped button, or None when the model answers this message as before."""
    if req.button not in BUTTONS or not req.conversation_id:
        return None
    from app.db import supabase as db

    cid = req.conversation_id
    conv = await db.get_conversation_simple(cid)
    if not conv:
        return None
    meta = conv.get("metadata") or {}
    meta = meta if isinstance(meta, dict) else {}
    if req.button == "yes":
        out = await _yes(cid, message, meta)
    elif req.button == "wait":
        out = await _wait(cid, message, meta, req.tunnel)
    else:
        out = await _channel(cid, req.button, message, meta, request_meta or {}, req.price_seen)
    if out is None:
        CARD_BUTTONS_HEALTH["fell_through"] += 1
    return out


async def _yes(cid: str, message: str, meta: dict) -> Optional[ChatResponse]:
    from app.db import supabase as db
    from app.services import card_words, proactive

    trigger = meta.get("proactive_trigger")
    trip = _trip(meta)
    if not trigger or not proactive.full_trip(trip):
        return None
    if await db.count_messages(cid, role="user") > 0:
        return None  # only the conversation's first answer: a later yes is the model's
    lang = _lang(meta)
    beat = proactive.applied_variant(trigger, trip, meta.get("proactive_variant")) == proactive.BEAT
    text = card_words.confirm(proactive.trip_line(trip, lang) or "", lang, beat=beat)
    buttons = card_words.confirm_buttons(lang, _team_online())
    await _save(cid, "user", message)
    row = await _save(cid, "ai", text, proactive.MODEL_USED)
    CARD_BUTTONS_HEALTH["yes"] += 1
    return _response(cid, row, text, buttons, proactive.MODEL_USED)


async def _channel(
    cid: str, kind: str, message: str, meta: dict, request_meta: dict, price_seen: Optional[str],
) -> Optional[ChatResponse]:
    from app.pipeline.entity_extractor import EMAIL_RE
    from app.services import card_words, lead_service
    from app.services.phone_country import country_hint

    country = country_hint({**meta, **request_meta})
    if kind == "whatsapp":
        from app.pipeline.callback_intent import find_phone

        contact = find_phone(message, country)
        if not contact:
            return None
        entities = {"phone": contact, "_phone_country": country}
    else:
        m = EMAIL_RE.search(message or "")
        if not m:
            return None
        contact = m.group(0).strip().lower()
        entities = {"email": contact}
    # The contact lands on the conversation row (the CRM gate, the abandoned push and the panel read it),
    # before anything is said: "Done — an agent will email you" only once it is kept. A failed write goes
    # to the model's turn instead, which saves the message and tries the contact again (CodeRabbit #270).
    if not await lead_service.update_lead_from_entities(cid, entities):
        CARD_BUTTONS_HEALTH["contact_not_saved"] += 1
        logger.error(f"[{cid}] card button: the {kind} contact was not saved, the model answers")
        return None
    await _save(cid, "user", message)
    await _note_channel(cid, kind, contact, price_from(price_seen))
    from app.services.routing import queue_for_person

    await queue_for_person(cid, reason=f"{kind} request")
    if kind == "whatsapp":
        from app.pipeline.orchestrator import _fire_and_forget
        from app.services.tasks_service import withdraw_call_tasks

        _fire_and_forget(withdraw_call_tasks(cid))
    text = card_words.channel_done(kind, contact, _lang(meta), _team_online())
    row = await _save(cid, "ai", text, MODEL_USED)
    CARD_BUTTONS_HEALTH[kind] += 1
    return _response(cid, row, text, [], MODEL_USED)


async def _note_channel(cid: str, kind: str, contact: str, price: Optional[str]) -> None:
    """The channel on the lead, where the panel and the CRM note read how to reach the client: the wish
    (intent_signals.contact_channel), the price the client saw when they said one, and for WhatsApp the
    line the consultant reads first (a WhatsApp, not a call)."""
    from app.db import supabase as db
    from app.pipeline.callback_intent import callback_note, notes_with_callback
    from app.pipeline.entity_extractor import merge_contact_channel
    from app.services import lead_service

    try:
        # Under the lead writer's lock (lead_service._lead_lock): the read, the merge and the write never
        # interleave with a model turn's, so neither writes an older intent_signals or notes over the
        # other's (CodeRabbit #270).
        async with lead_service._lead_lock(cid):
            lead = await lead_service.get_or_create_lead(cid)
            if not isinstance(lead, dict) or not lead.get("id"):
                logger.warning(f"[{cid}] card button: no lead to note the {kind} channel on")
                return
            raw = lead.get("intent_signals")
            signals = dict(raw) if isinstance(raw, dict) else ({} if not raw else {"legacy": raw})
            signals["contact_channel"] = merge_contact_channel(signals.get("contact_channel"), kind)
            if kind == "whatsapp":
                # A WhatsApp is not a call: the panel stops saying "Asks: a call back".
                signals["callback_requested"] = False
            if price:
                signals["price_seen"] = price
            update: dict = {"intent_signals": signals}
            if kind == "whatsapp":
                update["notes"] = notes_with_callback(lead.get("notes"), callback_note(contact, channel="whatsapp"))
            if not await db.update_lead(lead["id"], update):
                logger.warning(f"[{cid}] card button: channel not noted on the lead (no row written)")
    except Exception as e:  # noqa: silent — logged; the contact itself is already on the conversation
        logger.warning(f"[{cid}] card button: channel not noted on the lead ({type(e).__name__})")


async def _wait(cid: str, message: str, meta: dict, tunnel: str) -> ChatResponse:
    from app.db import supabase as db
    from app.services import card_words
    from app.services.routing import queue_for_person
    from config.settings import settings

    await _save(cid, "user", message)
    try:
        online = bool(await db.get_available_agents(tunnel=tunnel, timeout_seconds=settings.agent_timeout_seconds))
    except Exception as e:  # noqa: silent — logged; nobody known online means no minute promised
        logger.warning(f"[{cid}] card button: agents online unknown ({type(e).__name__})")
        online = False
    await queue_for_person(cid, reason="waits in the chat")
    text, buttons = card_words.wait_answer(_lang(meta), online)
    row = await _save(cid, "ai", text, MODEL_USED)
    CARD_BUTTONS_HEALTH["wait"] += 1
    return _response(cid, row, text, buttons, MODEL_USED)


# --- B1: before the client leaves ------------------------------------------

BEFORE_YOU_GO_KEY = "before_you_go_at"


_CLAIM_TRIES = 3
# The metadata read goes back in the URL as the UPDATE's condition, twice (@> and <@: the same object).
# 1.9k at most over 30 days (Nov 2026). Longer than this, the claim is not made — the message is not
# sent — rather than written under a weaker condition that could bring back a key another request removed
# (CodeRabbit #270, round 2); the URL stays well under a proxy's 8 KB.
_CLAIM_EQUAL_MAX = 2500


async def _claim(cid: str, key: str) -> bool:
    """Set metadata[key] once: True for the one caller that set it.

    The UPDATE's WHERE asks for the key's absence (one winner, as proactive._claim_opening) and for the
    metadata exactly as it was just read (@> and <@ together: the same object). A presence ping, or a flag
    another request set in between, makes it miss: the claim reads again instead of writing an older
    snapshot over them (CodeRabbit #270). The client is leaving while this runs, which is when the widget
    also says so (presence)."""
    from app.db import supabase as db

    try:
        client = db.get_client()
        for _ in range(_CLAIM_TRIES):
            res = await db._run_sync(
                lambda: client.table("conversations").select("metadata").eq("id", cid).single().execute()
            )
            raw = (res.data or {}).get("metadata")
            if raw is not None and not isinstance(raw, dict):
                return False
            existing = raw or {}
            if existing.get(key):
                return False
            payload = {**existing, key: datetime.now(timezone.utc).isoformat()}
            query = (
                client.table("conversations").update({"metadata": payload}).eq("id", cid)
                .is_(f"metadata->>{key}", "null")
            )
            if raw is None:
                query = query.is_("metadata", "null")
            else:
                seen = json.dumps(existing, separators=(",", ":"), ensure_ascii=False)
                if len(seen) > _CLAIM_EQUAL_MAX:
                    CARD_BUTTONS_HEALTH["before_you_go_too_long"] += 1
                    logger.warning(f"[{cid}] before-you-go claim: metadata too long to compare, not sent")
                    return False
                query = query.contains("metadata", seen).contained_by("metadata", seen)
            res = await db._run_sync(lambda: query.execute(), idempotent=False)
            if res.data:
                return True
        logger.warning(f"[{cid}] before-you-go claim: the metadata kept changing, not sent")
        return False
    except Exception as e:  # noqa: silent — logged; unclaimed means not sent (never twice)
        logger.warning(f"[{cid}] before-you-go claim failed ({type(e).__name__}): not sent")
        return False


def _lead_trip(lead: Optional[dict]):
    """The trip the lead holds, for the line the message says back; None without a destination. A
    destination alone is enough: "London, not Paris" said before any origin names London (CodeRabbit #270)."""
    from app.models.trip_context import TripContext

    if not isinstance(lead, dict) or not lead.get("destination_code"):
        return None
    try:
        return TripContext(
            origin_code=lead.get("origin_code"), destination_code=lead.get("destination_code"),
            depart_date=lead.get("departure_date"), return_date=lead.get("return_date"),
            adults=lead.get("passengers"),
        )
    except Exception:  # noqa: silent — a lead the trip model refuses gives the message without a line
        return None


async def before_you_go(cid: str) -> dict:
    """B1: {"show": False, "why"} or {"show": True, "message", "buttons", "id", "created_at"}."""
    from app.db import supabase as db
    from app.services import card_words, lead_service, proactive

    def skip(why: str) -> dict:
        CARD_BUTTONS_HEALTH["before_you_go_skipped"] += 1
        return {"show": False, "why": why}

    conv = await db.get_conversation_simple(cid)
    if not conv:
        return skip("no_conversation")
    if (conv.get("visitor_email") or "").strip() or (conv.get("visitor_phone") or "").strip():
        return skip("has_contact")
    if conv.get("status") in ("closed", "completed"):
        return skip("closed")
    if await db.count_messages(cid, role="agent") > 0:
        return skip("agent_wrote")
    if await db.count_messages(cid, role="user") == 0:
        return skip("never_wrote")
    meta = conv.get("metadata") or {}
    meta = meta if isinstance(meta, dict) else {}
    if meta.get(BEFORE_YOU_GO_KEY) or not await _claim(cid, BEFORE_YOU_GO_KEY):
        return skip("already_said")
    lang = _lang(meta)
    # The trip as it is now: the lead's, where a client who changed it said so ("London, not Paris");
    # the page's search only while the lead has none (CodeRabbit #270).
    try:
        trip = _lead_trip(await lead_service.get_or_create_lead(cid))
    except Exception:  # noqa: silent — no lead read means the page's trip, or the message without a line
        trip = None
    if trip is None or not proactive.trip_line(trip, lang):
        trip = _trip(meta)
    text, buttons = card_words.before_you_go(proactive.trip_line(trip, lang), lang)
    row = await _save(cid, "ai", text, MODEL_USED)
    if row is None:
        return skip("not_saved")
    CARD_BUTTONS_HEALTH["before_you_go"] += 1
    return {
        "show": True, "message": row.get("content") or text, "buttons": buttons,
        "id": row.get("id"), "created_at": row.get("created_at"),
    }


async def goodbye_without_contact(
    cid: str, meta: dict, visitor, lead: Optional[dict], wishes: Optional[str] = None,
    before_save: Optional[Callable[[], Awaitable[Optional[ChatResponse]]]] = None,
) -> Optional[ChatResponse]:
    """A goodbye ("thank you", "bye") from a sales client who left no way to reach them, or None when a
    contact is on file (the usual goodbye is true then).

    19 Nov, Sydney → Amman: the client gave the whole trip, was asked for the email once, thanked twice, and
    was told "Your search is in expert hands. We'll be in touch soon" — with no email and no number, by
    nobody. Here the first goodbye gets the before-you-go ask with its WhatsApp and Email buttons, said once
    per chat whichever of the two says it first (BEFORE_YOU_GO_KEY: a claim that fails counts as said, as for
    the before-you-go); a later one, or one from a client who turned a channel down, an honest goodbye that
    promises nothing and asks nothing again (CodeRabbit #270).

    `before_save` (the turn's agent guard) is awaited just before the message is saved: a reply it returns
    (an agent took the chat while the turn ran) is returned instead, and the goodbye is not said."""
    from app.pipeline.entity_extractor import refused_contact_fields
    from app.services import card_words, proactive

    if (getattr(visitor, "email", None) or "").strip() or (getattr(visitor, "phone", None) or "").strip():
        return None
    if (meta or {}).get("phone_to_check"):
        return None  # a number the agent checks is a way to reach them
    meta = meta if isinstance(meta, dict) else {}
    lang = _lang(meta)
    if refused_contact_fields(wishes) or meta.get(BEFORE_YOU_GO_KEY) or not await _claim(cid, BEFORE_YOU_GO_KEY):
        text, buttons, kind = card_words.goodbye_without_contact(lang), [], "goodbye_no_contact"
    else:
        trip = _lead_trip(lead)
        if trip is None or not proactive.trip_line(trip, lang):
            trip = _trip(meta)
        text, buttons = card_words.before_you_go(proactive.trip_line(trip, lang), lang)
        kind = "goodbye_asked"
    if before_save is not None:
        held = await before_save()
        if held is not None:
            return held
    CARD_BUTTONS_HEALTH[kind] += 1
    row = await _save(cid, "ai", text, MODEL_USED)
    return _response(cid, row, text, buttons, MODEL_USED)
