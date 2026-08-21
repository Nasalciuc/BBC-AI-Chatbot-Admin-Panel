"""8-step pipeline orchestrator — THE BRAIN of the chatbot."""

import asyncio
import functools
import logging
from datetime import datetime, timezone
from typing import Optional

from config.settings import settings
from app.models.chat import ChatResponse, VisitorInfo
from app.models.kb import KBResult
from app.services import conversation_service, lead_service
from app.services.crm import check_crm_ready, submit_to_crm
from app.db import supabase as db
from app.pipeline.intent import detect_intent, Intent
from app.pipeline.generator import generate_response, GeneratedResponse
from app.pipeline.validator import validate_response
from app.ai.templates import get_template
from app.realtime.manager import manager

logger = logging.getLogger(__name__)

# Background tasks that don't block the response
_background_tasks: set = set()  # prevent garbage collection

# OPEN DOOR — the free-text answer to "anything that would make this trip
# perfect?" after the summary. A "no" is fine; a route restatement is NOT a
# must-have (that was the Savannah→Auckland pollution).
_OPEN_DOOR_NO = {
    "no", "nope", "nothing", "none", "that's all", "thats all",
    "all good", "no thanks", "nothing else",
}


def decide_open_door_reply(
    message: str,
    *,
    pending: bool,
    confirmed_this_turn: bool,
    has_trip_entities: bool,
) -> tuple[Optional[str], bool]:
    """One-turn open-door window. Returns (must_haves|None, clear_pending).

    The window is exactly one client turn after the summary is shown. A route
    restatement or booking correction is never a must-have — the normal entity
    flow already handles those. A graceful "no" clears the flag and stores nothing.
    """
    if not pending:
        return None, False
    # Window closes this turn regardless of what we capture.
    if confirmed_this_turn:
        return None, True
    stripped = (message or "").strip()
    lowered = stripped.lower().rstrip(".!")
    if lowered in _OPEN_DOOR_NO:
        return None, True
    if has_trip_entities:
        return None, True
    if not (3 < len(stripped) <= 200):
        return None, True
    return stripped, True


# ── Confirmation truth: the gate speaks the clients' languages ───────────
# Conv #1347 "Purnisa": summary shown → client typed "Urs" (mobile typo for
# "Yes") → neither list matched → the LLM improvised "everything's locked
# in" while confirmed_at stayed NULL. The deterministic gate stays — but it
# must understand yes/si/da/oui/ja, tolerate one-letter typos, and on ANY
# doubt ASK instead of letting the prose outrun the state.

CONFIRM_WORDS = {
    "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "correct", "right",
    "perfect", "exact", "confirmed", "confirm", "great", "absolutely",
    "da", "si", "claro", "correcto", "oui", "ja", "jawohl", "confirmo",
    "all good", "looks good", "thats right", "thats correct",
    "that works", "sounds good",
}
REJECT_WORDS = {
    "no", "nope", "not right", "not correct", "wrong", "incorrect", "change",
    "mal", "incorrecto", "cambiar", "falsch", "non", "nu", "gresit",
}


def _normalize_reply(text: str) -> str:
    """strip → lower → drop punctuation → strip diacritics ("Sí." → "si")."""
    import re as _re
    import unicodedata as _ud

    lowered = (text or "").strip().lower()
    no_punct = _re.sub(r"[^\w\s]", "", lowered)
    decomposed = _ud.normalize("NFKD", no_punct)
    return "".join(c for c in decomposed if not _ud.combining(c)).strip()


def _lev_leq1(a: str, b: str) -> bool:
    """Levenshtein distance ≤ 1 — tiny inline check, no dependency."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    # a is the shorter (or equal) string; allow one edit.
    i = j = edits = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1; j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if la == lb:
            i += 1  # substitution
        j += 1      # insertion into the shorter
    return edits + (lb - j) + (la - i) <= 1


# Words that turn a "yes" into a request. "yes but make it one way" is not a
# confirmation of the summary as shown — closing over it would push a wrong
# lead to a consultant.
_CORRECTION_MARKERS = {
    "but", "however", "actually", "except", "instead", "though", "although",
    "pero", "aber", "mais", "dar", "insa",
}

# A confirmation with a tail stays a confirmation only while the tail is
# short. "yes correct" and "yes thats right" agree; five more words are
# carrying content, and content belongs to the correction path — this also
# keeps a Spanish "si quieres cambiar la fecha…" from reading as agreement.
_MAX_CONFIRM_TOKENS = 4


def is_confirmation(normalized: str) -> bool:
    """Exact multilingual match, or one typo away from yes/si — and the same
    once more when the client agrees in more than one word.

    "Yes corewct" was read as a rejection on 18 Aug 2026 and got the client a
    re-ask: the whole string matched nothing, and two tokens are not one typo
    away from "yes". A client who opens with a yes has said yes.

    "Urs" is distance 2 from "yes" — deliberately still NOT a confirmation:
    it lands on the re-ask.
    """
    if normalized in CONFIRM_WORDS:
        return True
    if _lev_leq1(normalized, "yes") or _lev_leq1(normalized, "si"):
        return True

    tokens = normalized.split()
    if len(tokens) < 2 or len(tokens) > _MAX_CONFIRM_TOKENS:
        return False
    head = tokens[0]
    if not (head in CONFIRM_WORDS or _lev_leq1(head, "yes") or _lev_leq1(head, "si")):
        return False
    # "yes, but…" is a correction wearing a yes.
    return not any(t in _CORRECTION_MARKERS for t in tokens[1:])


def is_rejection(normalized: str) -> bool:
    return normalized in REJECT_WORDS


# Turns we refused to speak. Every count is a moment the bot was about to
# answer a question nobody asked — 18 Aug 2026 it invented "you plus some
# friends" out of nothing and turned a solo traveller into a 7-passenger lead.
PIPELINE_HEALTH: dict = {"phantom_turns_blocked": 0, "last_at": None}


def _is_phantom_turn(history: list) -> bool:
    """True when the last thing said in this conversation is already ours.

    The bot speaks ONLY in reply. If the newest message is an AI or agent
    message, there is no question on the table, and generating now produces a
    turn out of thin air: the model is handed a history whose last line is its
    own, and it invents a premise to continue from.
    """
    for msg in reversed(history or []):
        role = (msg or {}).get("role")
        if role == "user":
            return False          # a client message is the newest — answer it
        if role in ("ai", "agent"):
            return True           # we (or a human) spoke last — nothing to answer
    return False                  # only system messages, or none at all


def _fire_and_forget(coro):
    """Run coroutine in background without blocking pipeline."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(lambda t: (
        _background_tasks.discard(t),
        t.exception() and logger.warning(f"[bg] task failed: {t.exception()}"),
    ))
    return task


async def _persist_unambiguous_correction(cid: str, probe, *, drop_dates: bool) -> None:
    """A question about ONE field must not eat the rest of the message.

    "Make it 4 passengers, business, returning Oct 1 or Oct 8" asks which
    date — and used to discard the passengers and the cabin with it, so
    the client had to say them again (and often didn't). Everything
    unambiguous is written before the question goes out; only the fields
    the question is ABOUT are held back."""
    payload: dict = {}
    if probe.passengers:
        payload["passengers"] = probe.passengers
    if probe.cabin_class:
        payload["cabin_class"] = probe.cabin_class
    if not drop_dates:
        if probe.departure_date:
            payload["departure_date"] = probe.departure_date
        if probe.origin_code:
            payload["origin"] = probe.origin_code
        if probe.destination_code:
            payload["destination"] = probe.destination_code
    if not payload:
        return
    try:
        await lead_service.update_lead_from_entities(cid, payload)
        logger.info(f"[{cid}] Ask-turn kept: {', '.join(sorted(payload))}")
    except Exception as e:
        logger.warning(f"[{cid}] Ask-turn persistence failed: {e}")


async def _persist_fallback_reply(cid: str, text: str, reason: str) -> None:
    """Failure handlers MUST leave a trace the visitor and the admin can see.

    A pipeline that answers over HTTP but writes nothing to the DB produced
    the 12-Jun 'silent AI' incident: admin showed dead air, returning visitors
    saw an empty thread. Never raises.
    """
    try:
        row = await conversation_service.add_message(
            conversation_id=cid,
            role="ai",
            content=text,
            model_used="template_fallback",
            cost=0.0,
        )
        if row:
            logger.info(f"[{cid}] Fallback persisted ({reason})")
        else:
            logger.error(
                f"[{cid}] Fallback NOT persisted ({reason}) — add_message returned None"
            )
    except Exception as e:
        logger.error(
            f"[{cid}] Fallback persistence failed ({reason}): {e}",
            exc_info=True,
        )


async def process_message(
    conversation_id: Optional[str],
    message: str,
    tunnel: str,
    visitor: VisitorInfo,
    metadata: Optional[dict] = None,
    visitor_id: Optional[str] = None,
    skip_user_save: bool = False,
) -> ChatResponse:
    """Run the 8-step pipeline. Always returns a response — never crashes.

    skip_user_save=True (FIX-C reprocess path): the visitor message is already
    persisted in the DB — do not insert it again."""
    conv = None
    # Set after the AI message row is confirmed in DB; failure handlers
    # persist a fallback ONLY if this is still False (no duplicate post-save).
    _persist_state = {"ai_persisted": False}
    try:
        return await asyncio.wait_for(
            _pipeline(
                conversation_id,
                message,
                tunnel,
                visitor,
                metadata,
                visitor_id=visitor_id,
                _persist_state=_persist_state,
                skip_user_save=skip_user_save,
            ),
            timeout=settings.pipeline_timeout,
        )
    except asyncio.TimeoutError:
        cid = conversation_id or "unknown"
        logger.error(
            f"[{cid}] Pipeline timeout ({settings.pipeline_timeout}s)",
            exc_info=True,
        )
        fallback = get_template("ai_fallback", tunnel, visitor) or (
            "Let me connect you with a specialist right away."
        )
        if not _persist_state["ai_persisted"]:
            await _persist_fallback_reply(cid, fallback, "pipeline_timeout")
        return ChatResponse(
            conversation_id=cid,
            message=fallback,
            type="template_fallback",
            model_used="template",
        )
    except Exception as e:
        cid = conversation_id or "unknown"
        logger.error(f"[{cid}] Pipeline fatal error: {e}", exc_info=True)
        fallback = get_template("ai_fallback", tunnel, visitor) or (
            "Let me connect you with a specialist right away."
        )
        if not _persist_state["ai_persisted"]:
            await _persist_fallback_reply(cid, fallback, "pipeline_fatal")
        return ChatResponse(
            conversation_id=cid,
            message=fallback,
            type="template_fallback",
            model_used="template",
        )


async def reprocess_last_user_message(conversation_id: str) -> None:
    """FIX-C backstop: re-run the pipeline on the last user message when it
    never got a reply (it arrived while the conversation was in human mode and
    the operator never engaged, then fall_back_to_ai fired).

    The visitor message is already persisted — the pipeline runs with
    skip_user_save=True. There is no HTTP response to carry the reply, so a
    non-streaming AI reply is pushed over SSE; streaming replies were already
    delivered chunk-by-chunk + push_stream_end by the pipeline itself."""
    try:
        conv = await db.get_conversation(conversation_id)
        if not conv:
            return
        if (conv.get("mode") or "ai") != "ai":
            return  # operator (re)claimed meanwhile — never talk over a human
        msgs = await db.get_recent_messages(conversation_id, limit=1)
        last = msgs[-1] if msgs else None
        if not last or last.get("role") != "user":
            return
        _last_ts = last.get("created_at")
        visitor = VisitorInfo(
            name=conv.get("visitor_name"),
            email=conv.get("visitor_email"),
            phone=conv.get("visitor_phone"),
        )
        logger.info(
            f"[{conversation_id}] FIX-C: reprocessing unanswered visitor message"
        )
        resp = await process_message(
            conversation_id=conversation_id,
            message=last.get("content") or "",
            tunnel=conv.get("tunnel") or "sales",
            visitor=visitor,
            metadata=None,  # conv metadata already persisted; site resolves from it
            visitor_id=conv.get("visitor_id"),
            skip_user_save=True,
        )
        if resp and not resp.streaming and _last_ts:
            _new = await db.get_messages_after(conversation_id, after=_last_ts)
            for _m in _new:
                if _m.get("role") == "ai":
                    await manager.push(conversation_id, _m)
    except Exception as e:
        logger.error(
            f"[{conversation_id}] reprocess_last_user_message failed: {e}",
            exc_info=True,
        )


async def _pipeline(
    conversation_id: Optional[str],
    message: str,
    tunnel: str,
    visitor: VisitorInfo,
    metadata: Optional[dict],
    visitor_id: Optional[str] = None,
    _persist_state: Optional[dict] = None,
    skip_user_save: bool = False,
) -> ChatResponse:
    """Internal pipeline implementation with 8 steps."""
    import time
    from app.pipeline.entity_extractor import (
        derive_t0_persona,
        extract_entities,
        extract_kb_keywords,
    )

    pipeline_start = time.perf_counter()
    _crm_submitted_this_turn = False

    # ── STEP 1: GET/CREATE CONVERSATION ───────────────────────
    conv = await conversation_service.get_or_create_conversation(
        conversation_id=conversation_id,
        tunnel=tunnel,
        visitor=visitor,
        visitor_id=visitor_id,
        metadata=metadata,
    )
    if not conv or "id" not in conv:
        raise RuntimeError("Failed to create conversation")

    cid = conv["id"]
    logger.info(f"[{cid}] Pipeline start | tunnel={tunnel}")

    # Save user message immediately (never lose data).
    # FIX-C reprocess: the message is already in the DB — skip the insert.
    if skip_user_save:
        user_msg = None
        user_msg_id = None
    else:
        user_msg = await conversation_service.add_message(
            conversation_id=cid, role="user", content=message
        )
        user_msg_id = user_msg["id"] if user_msg and isinstance(user_msg, dict) else None

    # Step 2.5: Content moderation (non-blocking)
    try:
        from app.services.moderation import moderate_message
        asyncio.create_task(
            moderate_message(cid, message, sender_role="user"),
            name=f"moderate_{cid}",
        )
    except Exception as _mod_err:
        logger.warning(f"[{cid}] Moderation task failed to schedule: {_mod_err}")

    # ── STEP 2.6: Check flagged content — skip templates if abusive
    _skip_templates = False
    try:
        from app.services.moderation import detect_bad_words
        if detect_bad_words(message):
            _skip_templates = True
            logger.info(
                f"[{cid}] Bad words detected — will skip templates, AI responds with empathy"
            )
    except Exception as e:
        logger.warning(f"[{cid}] moderation check failed: {e}")

    # Fetch history (lead fetched later when needed — L359+)
    history = await db.get_recent_messages(cid, limit=10)

    # ── PHANTOM TURN GUARD ───────────────────────────────────
    # Only the re-entrant path can get here without contributing a client
    # message: FIX-C's backstop is fire-and-forget, and `fall_back_to_ai` can
    # fire more than once for the same conversation, so two of them can both
    # read "the visitor's last message has no reply yet" before either reply
    # exists. The second run then answers a message that was already answered,
    # and the client sees the bot talking to itself.
    #
    # The normal path is deliberately NOT gated: its user message is saved a
    # few lines above, so it is always the newest, and if that save ever fails
    # we still owe the client an answer.
    if skip_user_save and _is_phantom_turn(history):
        PIPELINE_HEALTH["phantom_turns_blocked"] += 1
        PIPELINE_HEALTH["last_at"] = datetime.now(timezone.utc).isoformat()
        logger.warning(
            f"[{cid}] phantom_turn_blocked: last message is already ours — "
            "refusing to generate a turn nobody asked for"
        )
        return ChatResponse(
            conversation_id=cid,
            message="",
            streaming=False,
            type="blocked",
            model_used="none",
        )

    logger.info(f"[{cid}] [PERF] setup: {(time.perf_counter() - pipeline_start) * 1000:.0f}ms")
    t_section = time.perf_counter()

    # ── STEP 2: AGENT CHECK (V3 placeholder) ─────────────────
    # V1: always AI mode. V3 will check agent availability here.

    # ── STEP 3: INTENT DETECTION ─────────────────────────────
    _user_msg_count = sum(1 for m in (history or []) if m.get("role") == "user")
    intent = detect_intent(message, metadata, user_msg_count=_user_msg_count)
    logger.info(f"[{cid}] Intent: {intent.value}")

    # Confirm override: if summary was shown and client confirms,
    # treat as CONFIRMED regardless of original intent classification.
    _conv_meta = (conv or {}).get("metadata") or {}
    # Brand-resolved phone for handoff messages (BCT uses 668-3009, not 322-7999)
    from app.ai.prompts import get_brand_vars as _get_brand
    _brand_phone = _get_brand(
        (metadata or {}).get("site") or _conv_meta.get("site")
    ).get("contact_phone", "+1 (888) 322-7999")
    _awaiting_correction_directive = False
    # Correction-turn state (post-summary). The main extraction at Step 4
    # reuses _correction_context so a value the probe resolved — a day-only
    # return, a bare "Return" — actually reaches the lead instead of being
    # re-extracted without context and lost.
    #
    # These five MUST be initialised here and not only in the branch that
    # sets them: Step 4 reads _correction_context on EVERY turn (line ~847),
    # so a turn that never enters the post-summary branch would otherwise
    # die with UnboundLocalError before the client ever gets an answer.
    # That is exactly what #204 caused by dropping this block.
    _correction_context: Optional[dict] = None
    _clear_return_date = False
    _suppress_return_date = False
    _multi_city_declared = False
    # An added leg describes ITSELF, not the trip: "return from Paris to
    # Sydney on nov 9" must not overwrite Marky's real route and departure.
    _suppress_leg_fields = False
    # A callback request outranks the confirmation wall: JOSEF asked to be
    # rung WHILE a summary was on the table, and the ambiguity re-ask
    # answered him first — the exact state his transcript was reported in.
    # Step 3.45 owns those turns.
    from app.pipeline.callback_intent import detect_callback_request as _cb_probe

    _callback_pending = _cb_probe(message)
    if _callback_pending and _conv_meta.get("summary_shown_at"):
        logger.info(f"[{cid}] Callback request during summary wait — it wins")

    if (
        _conv_meta.get("summary_shown_at")
        and not _conv_meta.get("confirmed_at")
        and not _callback_pending
    ):
        _normalized = _normalize_reply(message)
        # REJECTION comes FIRST — before the confirm listener and before any
        # other post-summary listener. Conv "Costa": the client answered "no"
        # to "Is everything correct?" and no branch existed to hear it.
        if is_rejection(_normalized):
            from app.ai.templates import get_template as _get_tpl

            logger.info(f"[{cid}] Summary REJECTED (matched '{_normalized}') — asking what to correct")
            _correction_text = _get_tpl("summary_correction", tunnel, visitor) or (
                "Thanks for catching that — what should I fix: "
                "the route, the dates, or the passengers?"
            )
            await conversation_service.add_message(
                conversation_id=cid,
                role="ai",
                content=_correction_text,
                model_used="template",
                cost=0.0,
            )
            if _persist_state is not None:
                _persist_state["ai_persisted"] = True
            return ChatResponse(
                conversation_id=cid,
                message=_correction_text,
                type="template",
                model_used="template",
            )
        elif is_confirmation(_normalized):
            intent = Intent.CONFIRMED
            logger.info(
                f"[{cid}] Intent override → CONFIRMED "
                f"(matched '{_normalized}' after summary)"
            )
        else:
            # NEITHER — ambiguity must never fall through to a free LLM turn
            # (conv #1347: "Urs" → Opus improvised "everything's locked in"
            # while confirmed_at stayed NULL).
            # Day-only dates ("returning on the 17th") resolve against the
            # summary's departure — the correction probe is the ONLY caller
            # that passes context.
            _lead_for_probe = None
            try:
                _lead_for_probe = await lead_service.get_or_create_lead(cid)
            except Exception:
                pass
            _probe_ctx = {
                "departure_date": (_lead_for_probe or {}).get("departure_date")
            }
            _probe = extract_entities(message, context=_probe_ctx)

            # What does this correction MEAN? (app/pipeline/corrections.py —
            # MARKY's third city, KAZUO's impossible pairs, ALISTAIR's
            # field-without-a-value.) Asks beat writes.
            from app.pipeline.corrections import (
                classify_correction,
                detect_field_only_correction,
            )

            _outcome = classify_correction(_probe, _lead_for_probe or {}, message)
            if _outcome.ask:
                from app.ai.templates import get_template as _get_tpl

                _ask_map = {
                    "date_choice": "ask_date_choice",
                    "return_before_departure": "ask_return_date",
                    "return_equals_departure": "ask_return_date",
                }
                _fmt = {}
                if _outcome.ask == "date_choice" and len(_outcome.ask_options) >= 2:
                    _fmt = {
                        "option_a": _outcome.ask_options[0],
                        "option_b": _outcome.ask_options[1],
                    }
                _ask_text = _get_tpl(
                    _ask_map[_outcome.ask], tunnel, visitor, **_fmt
                ) or "Could you confirm the exact dates for me?"
                if _outcome.ask == "return_before_departure":
                    _ask_text = (
                        "That return lands before the departure — "
                        "when would you fly back?"
                    )
                elif _outcome.ask == "return_equals_departure":
                    _ask_text = (
                        "Same day there and back — did you mean a later "
                        "return? When would you fly back?"
                    )
                logger.info(
                    f"[{cid}] Post-summary correction needs an answer "
                    f"({_outcome.ask}) — asking instead of rendering"
                )
                # Everything the client said that ISN'T in question stays.
                await _persist_unambiguous_correction(cid, _probe, drop_dates=True)
                await conversation_service.add_message(
                    conversation_id=cid, role="ai", content=_ask_text,
                    model_used="template", cost=0.0,
                )
                if _persist_state is not None:
                    _persist_state["ai_persisted"] = True
                return ChatResponse(
                    conversation_id=cid, message=_ask_text,
                    type="template", model_used="template",
                )
            # Deborah, TPA→SJU: "Round trip, returning on the 17th" DID set
            # trip_type — and the old gate threw the entity away. A cabin
            # change post-summary is equally a correction.
            _correction_fields = [
                k for k in (
                    "origin_code", "destination_code", "departure_date",
                    "return_date", "passengers", "trip_type", "cabin_class",
                ) if getattr(_probe, k)
            ]
            _has_correction_entities = bool(_correction_fields)
            if _has_correction_entities:
                # A correction ("March 7 instead"): re-open the summary state
                # so the pipeline updates the lead and Step 7.5 re-renders the
                # summary with the new facts.
                _meta_upd = dict(_conv_meta)
                _meta_upd.pop("summary_shown_at", None)
                _meta_upd.pop("open_door_pending", None)
                _meta_upd.pop("summary_reask_count", None)
                try:
                    await db.update_conversation(cid, {"metadata": _meta_upd})
                    _conv_meta = _meta_upd
                except Exception as e:
                    logger.warning(f"[{cid}] summary re-open failed: {e}")
                _awaiting_correction_directive = True
                # Step 4 re-extracts the SAME message; without the context
                # the probe used, a day-only return ("the 17th") or a bare
                # "Return" would be parsed away and never reach the lead.
                _correction_context = _probe_ctx
                _clear_return_date = _outcome.clear_return
                _suppress_return_date = _outcome.drop_return
                logger.info(
                    f"[{cid}] Post-summary correction — summary will re-render "
                    f"(triggered by: {', '.join(_correction_fields)})"
                )
                # MARKY: "And return from Paris to Sydney on nov 9" — a leg
                # between two cities the trip doesn't touch. It is ADDED,
                # never swapped in over the route he already gave.
                # Sticky, but never deaf: if the client now states a simple
                # trip type ("actually just a round trip"), that wins and
                # the multi-city latch is released — otherwise every later
                # correction was overwritten back to multi_city forever.
                if _probe.trip_type in ("one_way", "round_trip"):
                    if _conv_meta.get("multi_city_declared"):
                        _meta_clear = dict(_conv_meta)
                        _meta_clear.pop("multi_city_declared", None)
                        _meta_clear.pop("extra_legs", None)
                        try:
                            await db.update_conversation(cid, {"metadata": _meta_clear})
                            _conv_meta = _meta_clear
                        except Exception as e:
                            logger.warning(f"[{cid}] multi-city release failed: {e}")
                        logger.info(
                            f"[{cid}] Client restated a simple trip type "
                            f"({_probe.trip_type}) — multi-city released"
                        )
                elif _outcome.multi_city or _conv_meta.get("multi_city_declared"):
                    _multi_city_declared = True
                if _outcome.extra_leg:
                    _suppress_leg_fields = True
                    _legs = list(_conv_meta.get("extra_legs") or [])
                    if _outcome.extra_leg not in _legs:
                        _legs.append(_outcome.extra_leg)
                    _meta_legs = dict(_conv_meta)
                    _meta_legs["extra_legs"] = _legs
                    _meta_legs["multi_city_declared"] = True
                    try:
                        await db.update_conversation(cid, {"metadata": _meta_legs})
                        _conv_meta = _meta_legs
                    except Exception as e:
                        logger.warning(f"[{cid}] extra-leg persist failed: {e}")
                    logger.info(
                        f"[{cid}] Client-stated extra leg: "
                        f"{_outcome.extra_leg['from']}→{_outcome.extra_leg['to']}"
                    )

                # ALISTAIR: "round trip dates" carries a trip_type AND names
                # a field with no value. Persist the trip type, then ask for
                # the dates — re-rendering the identical summary answers
                # nothing (it happened to him three times).
                _field_named = detect_field_only_correction(message, _probe)
                if _field_named:
                    if _probe.trip_type and _lead_for_probe:
                        try:
                            _tt_payload = {"trip_type": _probe.trip_type}
                            if _outcome.clear_return:
                                _tt_payload["return_date"] = None
                            await db.update_lead(_lead_for_probe["id"], _tt_payload)
                        except Exception as e:
                            logger.warning(f"[{cid}] trip-type persist failed: {e}")
                    from app.ai.templates import get_template as _get_tpl_f

                    _field_text = _get_tpl_f(
                        f"ask_field_{_field_named}", tunnel, visitor
                    ) or "Of course — what should it be?"
                    logger.info(
                        f"[{cid}] Correction named '{_field_named}' with no value — asking"
                    )
                    await _persist_unambiguous_correction(
                        cid, _probe, drop_dates=(_field_named == "dates")
                    )
                    if _probe.time_preference and _lead_for_probe:
                        try:
                            _n = (_lead_for_probe.get("notes") or "").strip()
                            _l = f"Time preference: {_probe.time_preference}"
                            if _l not in _n:
                                await db.update_lead(
                                    _lead_for_probe["id"],
                                    {"notes": f"{_n}\n{_l}".strip()},
                                )
                        except Exception as e:
                            logger.warning(f"[{cid}] time-pref note failed: {e}")
                    await conversation_service.add_message(
                        conversation_id=cid, role="ai", content=_field_text,
                        model_used="template", cost=0.0,
                    )
                    if _persist_state is not None:
                        _persist_state["ai_persisted"] = True
                    return ChatResponse(
                        conversation_id=cid, message=_field_text,
                        type="template", model_used="template",
                    )
                # "17th am" — the morning/evening preference survives into
                # the lead notes even though the correction owns the turn.
                if _probe.time_preference and _lead_for_probe:
                    try:
                        _cur_notes = (_lead_for_probe.get("notes") or "").strip()
                        _tp_line = f"Time preference: {_probe.time_preference}"
                        if _tp_line not in _cur_notes:
                            await db.update_lead(
                                _lead_for_probe["id"],
                                {"notes": f"{_cur_notes}\n{_tp_line}".strip()},
                            )
                    except Exception as e:
                        logger.warning(f"[{cid}] time-preference note failed: {e}")
            else:
                from app.ai.templates import get_template as _get_tpl

                # ALISTAIR: "round trip dates" / "change the dates" names a
                # FIELD with no value. Re-rendering the identical summary
                # (what happened three times) answers nothing — ask for the
                # field he just named.
                _field_only = detect_field_only_correction(message, _probe)
                if _field_only:
                    _field_text = _get_tpl(
                        f"ask_field_{_field_only}", tunnel, visitor
                    ) or "Of course — what should it be?"
                    logger.info(
                        f"[{cid}] Post-summary field-only correction "
                        f"({_field_only}) — asking for the value"
                    )
                    await conversation_service.add_message(
                        conversation_id=cid, role="ai", content=_field_text,
                        model_used="template", cost=0.0,
                    )
                    if _persist_state is not None:
                        _persist_state["ai_persisted"] = True
                    return ChatResponse(
                        conversation_id=cid, message=_field_text,
                        type="template", model_used="template",
                    )

                logger.info(f"[{cid}] Post-summary ambiguity ('{_normalized[:30]}') — re-asking, not improvising")
                # A wall that repeats itself verbatim reads as broken: the
                # SECOND re-ask in a summary cycle teaches the format instead.
                _reask_n = int(_conv_meta.get("summary_reask_count") or 0)
                _reask_key = "summary_reask_2" if _reask_n >= 1 else "summary_reask"
                try:
                    _meta_reask = dict(_conv_meta)
                    _meta_reask["summary_reask_count"] = _reask_n + 1
                    await db.update_conversation(cid, {"metadata": _meta_reask})
                    _conv_meta = _meta_reask
                except Exception as e:
                    logger.warning(f"[{cid}] reask-count update failed: {e}")
                _reask_text = _get_tpl(_reask_key, tunnel, visitor) or (
                    "Just to confirm everything's correct — reply YES, "
                    "or tell me what to change."
                )
                await conversation_service.add_message(
                    conversation_id=cid,
                    role="ai",
                    content=_reask_text,
                    model_used="template",
                    cost=0.0,
                )
                if _persist_state is not None:
                    _persist_state["ai_persisted"] = True
                return ChatResponse(
                    conversation_id=cid,
                    message=_reask_text,
                    type="template",
                    model_used="template",
                )
    _confirmed_this_turn = intent == Intent.CONFIRMED

    # Merged view of where this visitor came from — widget metadata for this
    # turn on top of what the conversation already knows.
    _site_metadata = {**_conv_meta, **(metadata or {})}
    if visitor and getattr(visitor, "country_code", None):
        _site_metadata.setdefault("phone_country", visitor.country_code)
        _site_metadata.setdefault("country_code", visitor.country_code)

    # ── STEP 3.6: MULTI-TURN PROBE DETECTION ─────────────────
    _probe_keywords = [
        "guidelines", "instructions", "system prompt", "your rules",
        "who made you", "what model", "what version", "your prompt",
        "anthropic", "how were you trained", "your programming",
    ]
    if history:
        probe_count = sum(
            1 for m in history if m.get("role") == "user" and
            any(kw in m.get("content", "").lower() for kw in _probe_keywords)
        )
        if probe_count >= 3:
            logger.warning(f"[{cid}] SECURITY: Multi-turn probe detected ({probe_count} probes in history)")

    # ── STEP 3.45: CALLBACK REQUEST ──────────────────────────
    # JOSEF typed his number and asked us to ring him; the pipeline asked
    # what he meant. A callback request is never ambiguous — hear it,
    # keep the number, queue a human, and carry on collecting.
    from app.pipeline.callback_intent import callback_note

    _callback = _callback_pending
    if _callback:
        logger.info(f"[{cid}] Callback requested (cue={_callback['cue']})")
        try:
            _cb_lead = await lead_service.get_or_create_lead(cid)
            if _cb_lead:
                _cb_signals = _cb_lead.get("intent_signals")
                _cb_signals = dict(_cb_signals) if isinstance(_cb_signals, dict) else {}
                _cb_signals["callback_requested"] = True
                _cb_note = callback_note(_callback["number"])
                _cb_notes = (_cb_lead.get("notes") or "").strip()
                _cb_payload: dict = {"intent_signals": _cb_signals}
                if _cb_note not in _cb_notes:
                    # The consultant must see it FIRST, above everything else.
                    _cb_payload["notes"] = f"{_cb_note}\n{_cb_notes}".strip()
                if _callback["number"] and not _cb_lead.get("visitor_phone"):
                    _cb_payload["visitor_phone"] = _callback["number"]
                await db.update_lead(_cb_lead["id"], _cb_payload)
        except Exception as e:
            logger.warning(f"[{cid}] callback note failed: {e}")
        # A human owns this now — same queue the explicit agent request uses.
        try:
            from app.services.routing import dispatch_needs_agent

            if (conv or {}).get("status") != "needs_agent":
                await db.update_conversation(cid, {"status": "needs_agent"})
            _fire_and_forget(dispatch_needs_agent(cid))
        except Exception as e:
            logger.warning(f"[{cid}] callback dispatch failed: {e}")

        # Confirm warmly and keep collecting — deterministic, because the
        # one thing this turn must never do is ask him what he meant.
        from app.ai.templates import get_template as _cb_tpl

        if _callback["number"]:
            _cb_text = _cb_tpl(
                "callback_confirmed", tunnel, visitor, number=_callback["number"]
            ) or f"Of course — a consultant will call you on {_callback['number']}."
            _cb_next = ""
            try:
                from app.models.lead import get_missing_fields as _cb_gmf

                _cb_missing = _cb_gmf(_cb_lead or {}, {
                    "visitor_name": getattr(visitor, "name", None),
                    "visitor_email": getattr(visitor, "email", None),
                    "visitor_phone": _callback["number"],
                })
                _cb_ask_map = {
                    "travel dates": "ask_dates", "departure date": "ask_dates",
                    "email": "ask_email", "name": "ask_name",
                    "passengers": "ask_passengers",
                }
                for _m in _cb_missing:
                    _k = next((v for k, v in _cb_ask_map.items() if k in _m), None)
                    if _k:
                        _q = _cb_tpl(_k, tunnel, visitor)
                        if _q:
                            _cb_next = f" While they get to you — {_q[0].lower()}{_q[1:]}"
                            break
            except Exception:
                pass
            _cb_text = f"{_cb_text}{_cb_next}"
        else:
            _cb_text = _cb_tpl("callback_confirmed_no_number", tunnel, visitor) or (
                "Of course — a consultant will call you. "
                "What's the best number to reach you on?"
            )
        await conversation_service.add_message(
            conversation_id=cid, role="ai", content=_cb_text,
            model_used="template", cost=0.0,
        )
        if _persist_state is not None:
            _persist_state["ai_persisted"] = True
        return ChatResponse(
            conversation_id=cid, message=_cb_text,
            type="template", model_used="template",
        )

    # ── STEP 3.5: AGENT HANDOFF CHECK ────────────────────────
    if intent == Intent.TALK_TO_AGENT and history:
        agent_keywords = ["agent", "human", "person", "someone", "speak", "talk to", "representative", "real person"]
        agent_request_count = sum(
            1 for m in history
            if m.get("role") == "user" and any(
                kw in m.get("content", "").lower() for kw in agent_keywords
            )
        )
        # The FIRST explicit request counts. The old 2nd-request gate meant
        # "Agent, please" got a phone number and the demand signal died —
        # almost nobody asks twice.
        if agent_request_count >= 0:
            try:
                await db.update_conversation(cid, {"status": "needs_agent"})
                logger.info(f"[{cid}] HANDOFF: agent request #{agent_request_count + 1} → status=needs_agent")
                from app.services.routing import dispatch_needs_agent
                _fire_and_forget(dispatch_needs_agent(cid))
            except Exception as e:
                logger.warning(f"[{cid}] Failed to set needs_agent status: {e}")

    logger.info(f"[{cid}] [PERF] intent: {(time.perf_counter() - t_section) * 1000:.0f}ms")
    t_section = time.perf_counter()

    # ── STEP 4: ENTITY EXTRACTION ────────────────────────────
    # _correction_context is set ONLY on a post-summary correction turn, so
    # the value the probe resolved (day-only return, bare "Return") lands in
    # the lead instead of being parsed away a second time without context.
    if _correction_context is None and _conv_meta.get("awaiting_return_date"):
        # We just asked "when would you fly back?" — a bare date in the
        # answer is the RETURN. Without this it was read as a new
        # departure and walked the outbound forward, re-asking forever.
        _correction_context = {
            "departure_date": _conv_meta.get("awaiting_return_departure"),
            "expect": "return",
        }
    extracted = extract_entities(message, context=_correction_context)
    if _conv_meta.get("awaiting_return_date") and extracted.return_date:
        _meta_clear_rt = dict(_conv_meta)
        _meta_clear_rt.pop("awaiting_return_date", None)
        _meta_clear_rt.pop("awaiting_return_departure", None)
        _meta_clear_rt.pop("return_ask_count", None)
        try:
            await db.update_conversation(cid, {"metadata": _meta_clear_rt})
            _conv_meta = _meta_clear_rt
        except Exception as e:
            logger.warning(f"[{cid}] return-ask clear failed: {e}")

    # OPEN DOOR: one-turn window after the summary. Needs the extractor's
    # trip fields so a route restatement is never stored as a must-have.
    _has_trip = bool(
        extracted.origin_code
        or extracted.destination_code
        or extracted.departure_date
        or extracted.passengers
    )
    _open_door_reply, _clear_open_door = decide_open_door_reply(
        message,
        pending=bool(_conv_meta.get("open_door_pending")),
        confirmed_this_turn=_confirmed_this_turn,
        has_trip_entities=_has_trip,
    )
    if _clear_open_door:
        _meta_upd = dict(_conv_meta)
        _meta_upd["open_door_pending"] = False
        try:
            await db.update_conversation(cid, {"metadata": _meta_upd})
            _conv_meta = _meta_upd
            _site_metadata = {**_conv_meta, **(metadata or {})}
        except Exception as e:
            logger.warning(f"[{cid}] Failed to clear open_door_pending: {e}")
        if _open_door_reply:
            logger.info(f"[{cid}] Open-door must-have captured ({len(_open_door_reply)} chars)")
        else:
            logger.info(f"[{cid}] Open-door window closed (no must-have stored)")

    entities: dict = {
        # HARD RULE while a summary awaits confirmation: the LLM never speaks
        # "done". The state machine owns confirmation; prose must not outrun
        # it (conv #1347: improvised "everything's locked in" on NULL state).
        # NOTE: the directive is the conditional part, never the message —
        # `a + b if c else ""` binds as `(a + b) if c else ""` and erased
        # the client's message on every non-correction turn (API 400
        # "messages.0: user messages must have non-empty content").
        "_raw_message": message + (
            (
                "\n\n(SYSTEM: the booking summary is awaiting the client's "
                "explicit confirmation. Do NOT use confirmation or closing "
                "language — no 'locked in', no 'all set', no 'everything's "
                "confirmed', no consultant-call promises. Acknowledge the "
                "correction; the system will re-show the summary.)"
            ) if _awaiting_correction_directive else ""
        ),
        # name: ALWAYS from visitor form data — never extract from message text
        # extracting name from message causes "looking for" or other text fragments
        # to override the real visitor name submitted in the form
        "name": visitor.name if visitor.name else None,
        "email": extracted.email or (visitor.email if visitor.email else None),
        "phone": extracted.phone or (visitor.phone if visitor.phone else None),
        # A client-stated extra leg describes the LEG — writing its cities
        # and date onto the trip is how MARKY's original route vanished.
        "origin": None if _suppress_leg_fields else extracted.origin_code,
        "destination": None if _suppress_leg_fields else extracted.destination_code,
        "passengers": extracted.passengers,
        "cabin_class": extracted.cabin_class,
        "departure_date": None if _suppress_leg_fields else extracted.departure_date,
        # KAZUO: an explicit one-way, an impossible pair or a "X or Y"
        # choice must never leave a return date behind.
        "return_date": None if _suppress_return_date else extracted.return_date,
        "trip_type": "multi_city" if _multi_city_declared else extracted.trip_type,
        "itinerary": extracted.itinerary,
        "_children_count": extracted.children_count or 0,
        "_infant_count": extracted.infant_count or 0,
        # Sales-methodology signals — merged into the lead's intent_signals and
        # handed to the consultant; _metadata carries SITE CONTEXT to the prompt.
        "occasion": extracted.occasion,
        "booking_for": extracted.booking_for,
        "date_flexible": extracted.date_flexible,
        "airline_preference": extracted.airline_preference,
        "airline_avoid": extracted.airline_avoid,
        "nonstop_only": extracted.nonstop_only,
        "budget_hint": extracted.budget_hint,
        "price_seen": extracted.price_seen,
        "best_call_time": extracted.best_call_time,
        "must_haves": _open_door_reply,
        "_metadata": _site_metadata,
    }

    # An explicit one-way wipes a stale return date on the lead (KAZUO:
    # the summary kept showing a return he had just cancelled).
    if _clear_return_date:
        entities["_clear_return_date"] = True
    # MARKY's client-stated legs lead the notes so the consultant reads the
    # real shape of the trip before anything else.

    # Prefer a persona already confirmed on the lead (history) over marketing priors.
    _history_persona = None
    _history_confidence = None
    _occasion_for_persona = extracted.occasion
    _prior_lead = None
    try:
        _prior_lead = await lead_service.get_or_create_lead(cid)
        _prior_signals = (_prior_lead or {}).get("intent_signals")
        if isinstance(_prior_signals, dict):
            _history_persona = _prior_signals.get("persona")
            _history_confidence = _prior_signals.get("persona_confidence")
            if not _occasion_for_persona:
                _occasion_for_persona = _prior_signals.get("occasion")
    except Exception as _lead_peek_err:  # noqa: BLE001
        logger.warning(f"[{cid}] Lead peek for persona history failed: {_lead_peek_err}")

    # MARKY's client-stated legs lead the consultant's note. The base route
    # comes from the LEAD, never from this message: on the leg turn the
    # message's cities ARE the leg, so reading them here wrote the leg
    # twice and dropped the real route. A full chain typed by the client
    # (extractor `itinerary`) wins outright.
    if _conv_meta.get("extra_legs") and not extracted.itinerary:
        _leg_parts = [
            f"{leg.get('from', '?')}→{leg.get('to', '?')}"
            + (f" {leg['date']}" if leg.get("date") else "")
            for leg in _conv_meta["extra_legs"]
        ]
        _base_route = " → ".join(
            p for p in (
                (_prior_lead or {}).get("origin_code"),
                (_prior_lead or {}).get("destination_code"),
            ) if p
        )
        entities["itinerary"] = (
            f"{', '.join(_leg_parts)} (client-stated)"
            if not _base_route
            else f"{_base_route}, {', '.join(_leg_parts)} (client-stated)"
        )

    _persona, _persona_source, _persona_confidence = derive_t0_persona(
        _site_metadata,
        occasion=_occasion_for_persona,
        message_text=message,
        visitor_email=(visitor.email if visitor else None),
        history_persona=(
            _history_persona if _history_confidence == "frame" else None
        ),
    )
    if _persona:
        # Stamp SITE CONTEXT always; persist to the lead only at frame confidence
        # so a soft tint cannot poison the next turn's history prior.
        _site_metadata["_persona"] = _persona
        _site_metadata["_persona_source"] = _persona_source
        _site_metadata["_persona_confidence"] = _persona_confidence
        entities["_metadata"] = _site_metadata
        if _persona_confidence == "frame":
            entities["persona"] = _persona
            entities["persona_source"] = _persona_source
            entities["persona_confidence"] = _persona_confidence

    # IATA LLM fallback removed (PR1) — TRAVEL_TOOL in generate extracts
    # origin/destination as superset. Route captured via tool_entities merge
    # at Step 6.1, then CRM re-check at Step 6.2.

    # ── KB search started in parallel (independent of entities/lead) ──
    _kb_task = None
    _skip_kb = intent in (Intent.GREETING, Intent.CLOSING, Intent.TALK_TO_AGENT)
    if not _skip_kb:

        async def _fetch_kb():
            try:
                if settings.qdrant_enabled and settings.qdrant_url:
                    from app.db.qdrant import search_kb as qdrant_search_kb
                    raw = await qdrant_search_kb(message, tunnel=tunnel, limit=3)
                    if raw:
                        return [
                            KBResult(
                                entry_id=r["id"],
                                title=r["title"],
                                content=r["content"],
                                score=r.get("score", 0.8),
                                source="vector",
                            )
                            for r in raw
                        ]
                return []
            except Exception as e:
                logger.warning(f"[{cid}] KB parallel search failed: {e}")
                return []

        _kb_task = asyncio.create_task(_fetch_kb())

    # Update lead with all extracted entities
    has_useful = any(
        entities.get(k)
        for k in [
            "name",
            "email",
            "phone",
            "origin",
            "destination",
            "departure_date",
            "return_date",
            "trip_type",
            "passengers",
            "cabin_class",
        ]
    )
    # Signals alone are worth a write when a lead already exists — the occasion
    # usually arrives on a turn that carries no new travel field.
    has_signals = any(entities.get(k) for k in lead_service.SIGNAL_KEYS)
    if has_useful or has_signals:
        await lead_service.update_lead_from_entities(cid, entities)
        logger.info(f"[{cid}] Entities: {', '.join(k for k, v in entities.items() if v and not k.startswith('_'))}")

    logger.info(f"[{cid}] [PERF] extraction: {(time.perf_counter() - t_section) * 1000:.0f}ms")
    t_section = time.perf_counter()

    # ── STEP 4.5: CRM — deferred to Step 6.2 (single submit point) ──
    # Removed pre-generate CRM to prevent race conditions and premature submit.

    logger.info(f"[{cid}] [PERF] crm: {(time.perf_counter() - t_section) * 1000:.0f}ms")
    t_section = time.perf_counter()

    # ── STEP 5: KB results (started earlier in parallel) ──────
    kb_results: list[KBResult] = []
    if _kb_task:
        kb_results = await _kb_task
    elif not _skip_kb:
        # Keyword fallback (Qdrant disabled)
        keywords = extract_kb_keywords(message)
        if keywords:
            raw_results = await db.keyword_search_kb(keywords, tunnel=tunnel, limit=3)
            kb_results = [
                KBResult(
                    entry_id=r["id"],
                    title=r["title"],
                    content=r["content"],
                    score=1.0,
                    source="keyword",
                )
                for r in raw_results
            ]

    kb_source = kb_results[0].source if kb_results else "none"
    logger.info(f"[{cid}] KB: {len(kb_results)} results (source: {kb_source})")

    logger.info(f"[{cid}] [PERF] kb_search: {(time.perf_counter() - t_section) * 1000:.0f}ms")
    t_section = time.perf_counter()

    # ── STEP 6: GENERATE RESPONSE ────────────────────────────
    # history already fetched at line 92 — reuse (saves ~400ms roundtrip)
    # Lead may already be loaded for persona history — avoid a second round-trip.
    if _prior_lead is not None:
        today_cost = await db.get_today_cost()
        lead = _prior_lead
        # Refresh after entity write so the prompt sees this turn's signals.
        if has_useful or has_signals:
            try:
                lead = await lead_service.get_or_create_lead(cid)
            except Exception as _lead_refresh_err:  # noqa: BLE001
                logger.warning(f"[{cid}] Lead refresh failed: {_lead_refresh_err}")
    else:
        lead, today_cost = await asyncio.gather(
            lead_service.get_or_create_lead(cid),
            db.get_today_cost(),
        )
    budget_remaining = settings.daily_budget - today_cost

    # Streaming callback (sync→async bridge for thread pool)
    _event_loop = asyncio.get_running_loop()
    _is_streaming = False

    def _on_chunk(delta: str):
        nonlocal _is_streaming
        _is_streaming = True
        asyncio.run_coroutine_threadsafe(
            manager.push_chunk(cid, delta),
            _event_loop,
        )

    if metadata and metadata.get("site"):
        entities["site"] = metadata["site"]

    _gen_fn = functools.partial(
        generate_response,
        intent=intent,
        entities=entities,
        kb_results=kb_results,
        visitor=visitor,
        lead=lead,
        history=history,
        tunnel=tunnel,
        budget_remaining=budget_remaining,
        skip_templates=_skip_templates,
        on_chunk=_on_chunk,
        conversation_id=cid,
    )
    gen = await asyncio.to_thread(_gen_fn)
    logger.info(f"[{cid}] Generated via {gen.model_used} | cost=${gen.cost:.4f}")

    # ── STEP 6.05: Process [HANDOFF_REQUESTED] token
    if gen and gen.text and "[HANDOFF_REQUESTED]" in gen.text:
        logger.info(f"[{cid}] AI requested handoff — checking availability")
        _saved_tool_entities = gen.tool_entities
        try:
            from app.services.handoff import (
                _handoff_phrase_recently_sent,
                perform_handoff_to_agent,
            )
            from app.services.routing import route_conversation
            from app.ai.templates import get_template

            if await _handoff_phrase_recently_sent(cid):
                gen = GeneratedResponse(
                    text=(
                        "I understand you'd like to speak with someone. "
                        f"You can reach us directly at {_brand_phone} — available 24/7."
                    ),
                    model_used="template",
                )
                if _saved_tool_entities:
                    gen.tool_entities = _saved_tool_entities
            else:
                route_result = await route_conversation(
                    tunnel, visitor=visitor, visitor_id=visitor_id
                )
            if (
                gen.model_used != "template"
                and route_result
                and route_result.get("agent_id")
            ):
                await perform_handoff_to_agent(
                    cid,
                    agent_id=route_result["agent_id"],
                    agent_name=route_result.get("agent_name", "A specialist"),
                    tunnel=tunnel,
                    emit_messages=True,
                    handoff_reason="visitor_request",
                )
                gen = GeneratedResponse(
                    text=(
                        "I've connected you with a specialist. They have all the "
                        "details from our conversation."
                    ),
                    model_used="handoff",
                )
                if _saved_tool_entities:
                    gen.tool_entities = _saved_tool_entities
            elif gen.model_used != "template":
                # Queue feeding (V2): the client asked for a human and none is
                # available THIS second — mark the conversation as waiting so the
                # FIRST operator who comes online receives it (silent reservation).
                # AI keeps serving meanwhile (mode stays 'ai').
                try:
                    await db.update_conversation(cid, {"status": "needs_agent"})
                    logger.info(f"[{cid}] No agent available → status=needs_agent (queued)")
                except Exception as e:
                    logger.error(
                        f"[{cid}] Failed to queue needs_agent: {e}",
                        exc_info=True,
                    )
                no_agent = get_template("no_agent_available", tunnel, visitor)
                gen = GeneratedResponse(
                    text=no_agent or (
                        "All specialists are currently busy. "
                        f"Please call {_brand_phone}."
                    ),
                    model_used="template",
                )
                if _saved_tool_entities:
                    gen.tool_entities = _saved_tool_entities

                # Alert super@ when no agents are available (fire-and-forget, throttled)
                _presence = _conv_meta.get("widget_presence")
                if _presence != "left":

                    async def _send_super_alert():
                        from app.services.closing import claim_super_alert
                        from app.services.email import send_super_alert_email

                        claimed = await claim_super_alert(
                            cid, settings.super_alert_cooldown_minutes
                        )
                        if claimed:
                            await send_super_alert_email(
                                conversation_id=cid,
                                visitor_name=visitor.name if visitor else None,
                                visitor_phone=visitor.phone if visitor else None,
                                visitor_email=visitor.email if visitor else None,
                                tunnel=tunnel,
                                last_message=message,
                                chat_number=(conv or {}).get("chat_number"),
                            )

                    _fire_and_forget(_send_super_alert())
        except Exception as e:
            logger.error(f"[{cid}] Handoff routing error: {e}", exc_info=True)
            gen = GeneratedResponse(
                text=f"For immediate assistance, please call {_brand_phone}.",
                model_used="template",
            )
            if _saved_tool_entities:
                gen.tool_entities = _saved_tool_entities

    # ── STEP 6.1: MERGE CLAUDE TOOL ENTITIES ─────────────────
    if gen.tool_entities:
        _te = gen.tool_entities
        _merged = False
        # The tool reads the SAME client message and its prompt says "use
        # the LAST mentioned value" — so on an added-leg turn it happily
        # returns the leg's cities and date. Without this the suppressions
        # above are undone here and MARKY's route dies anyway.
        _tool_blocked = set()
        if _suppress_leg_fields:
            _tool_blocked |= {"origin", "destination", "departure_date", "return_date"}
        if _clear_return_date or _suppress_return_date:
            _tool_blocked.add("return_date")
        if _multi_city_declared:
            _tool_blocked.add("trip_type")
        for key in [
            "origin",
            "destination",
            "departure_date",
            "return_date",
            "trip_type",
            "passengers",
            "cabin_class",
        ]:
            if key in _tool_blocked:
                continue
            val = _te.get(key)
            if val is None:
                continue
            if isinstance(val, str) and not val.strip():
                continue
            if key in ("origin", "destination"):
                if not isinstance(val, str):
                    continue
                val = val.strip().upper()
                if not (len(val) == 3 and val.isalpha()):
                    continue
            if key == "passengers":
                if isinstance(val, str) and val.strip().isdigit():
                    val = int(val.strip())
                elif isinstance(val, float) and val.is_integer():
                    val = int(val)
                if not (isinstance(val, int) and 1 <= val <= 9):
                    continue
            if key == "trip_type":
                if not isinstance(val, str):
                    continue
                val = val.strip().lower().replace("-", "_")
                if val not in ("one_way", "round_trip"):
                    continue
            if key == "cabin_class":
                if not isinstance(val, str):
                    continue
                val = val.strip().lower().replace(" ", "_")
                if val not in ("business", "first", "premium_economy"):
                    continue
            if key in ("departure_date", "return_date") and isinstance(val, str):
                val = val.strip()
            # Protect user-provided data from tool overwrite
            if key in ("passengers", "cabin_class") and entities.get(key):
                logger.info(
                    f"[{cid}] Tool {key}={val} skipped — user already set {entities[key]}"
                )
                continue
            entities[key] = val
            _merged = True

        if _merged:
            await lead_service.update_lead_from_entities(cid, entities)
            logger.info(f"[{cid}] Claude extraction merged: {list(_te.keys())}")

    # STEP 6.1.5 (RECONCILE FROM AI RESPONSE TEXT) is DELETED, permanently.
    # Lead facts come from the CLIENT's messages only. The AI's own text is
    # output, not evidence. Conv "Costa": the AI wrote "available 24/7",
    # this step parsed it as July 24, invented departure_date 2027-07-24,
    # the dates question got skipped, and a phantom itinerary shipped.

    # ── STEP 6.2: CRM SUBMIT (single point — after merge) ─────
    if tunnel == "sales" and settings.crm_api_url:
        try:
            _lead_fresh = await lead_service.get_or_create_lead(cid)
            if not _lead_fresh:
                logger.warning(f"[{cid}] CRM skip: no lead row exists")
            elif _lead_fresh.get("created_in_crm"):
                logger.info(f"[{cid}] CRM skip: already submitted")
            elif not check_crm_ready(_lead_fresh, visitor):
                from app.models.lead import get_missing_fields

                _miss = get_missing_fields(
                    _lead_fresh,
                    {
                        "visitor_name": getattr(visitor, "name", None),
                        "visitor_email": getattr(visitor, "email", None),
                        "visitor_phone": getattr(visitor, "phone", None),
                    },
                    for_crm=True,
                )
                logger.warning(f"[{cid}] CRM skip: missing={_miss}")
            else:
                _already_confirmed = _conv_meta.get("confirmed_at") or _confirmed_this_turn
                if not _already_confirmed:
                    logger.info(f"[{cid}] CRM skip: awaiting client confirmation")
                else:
                    if _confirmed_this_turn and not _conv_meta.get("confirmed_at"):
                        _meta_upd = dict(_conv_meta)
                        _meta_upd["confirmed_at"] = datetime.now(timezone.utc).isoformat()
                        # Open-door arms HERE (post-confirmation), one-turn
                        # window as before — a summary "no" can no longer be
                        # swallowed by the graceful-no list.
                        _meta_upd["open_door_pending"] = True
                        await db.update_conversation(cid, {"metadata": _meta_upd})
                        _conv_meta = _meta_upd
                    _client_ip = _conv_meta.get("client_ip")
                    _suid = visitor_id or _conv_meta.get("visitor_id")
                    _crm = await submit_to_crm(
                        _lead_fresh, visitor, cid,
                        conv_metadata=_conv_meta,
                        client_ip=_client_ip,
                        suid=_suid,
                    )
                    if _crm.success:
                        _marked = await db.mark_lead_created_in_crm(_lead_fresh["id"])
                        if _marked is None:
                            # The CRM row EXISTS but our flag write died. The
                            # backstop and the abandoned cron have checked this
                            # since day one; this path did not, and that is why
                            # thirteen confirmed conversations were pushed a
                            # second time days later, and one of them six times.
                            # Loud, and NOT counted as submitted: a lead we
                            # cannot mark is a lead the crons must be told about.
                            logger.error(
                                f"[{cid}] CRM ACCEPTED lead={_lead_fresh['id']} "
                                "but the flag write FAILED — this conversation "
                                "will be re-pushed by the cron unless fixed by hand"
                            )
                        else:
                            _crm_submitted_this_turn = True
                        logger.info(f"[{cid}] CRM submitted after client confirmation")
        except Exception as e:
            logger.error(f"CRM re-check error (non-blocking): {e}")

    logger.info(f"[{cid}] [PERF] generate: {(time.perf_counter() - t_section) * 1000:.0f}ms")
    t_section = time.perf_counter()

    # ── STEP 7: VALIDATE OUTPUT ──────────────────────────────
    # Skip validation for template responses (trusted content).
    # Only validate AI-generated text (Haiku/Sonnet).
    if gen.model_used == "template":
        validated_text = gen.text
    else:
        validated_text = validate_response(gen.text)

    logger.info(f"[{cid}] [PERF] validate: {(time.perf_counter() - t_section) * 1000:.0f}ms")
    t_section = time.perf_counter()

    # ── STEP 8: DELIVER ──────────────────────────────────────
    # Refusal detection (prevents magic string persistent DoS)
    _refusal_signals = [
        "i can't assist", "i cannot assist", "i'm not able to",
        "i can't help with", "i cannot help with",
        "i must decline", "i'm unable to",
    ]
    _is_refusal = any(s in validated_text.lower() for s in _refusal_signals)
    if _is_refusal and len(validated_text) < 100:
        logger.warning(f"[{cid}] Claude refusal detected — using fallback")
        validated_text = get_template("ai_fallback", tunnel, visitor) or (
            "Let me connect you with a specialist who can help with that right away."
        )

    # Guard: if an agent claimed this conversation while the pipeline was running,
    # discard the AI response — never let AI and human work in parallel.
    # Exception: silent reservation (announce_pending) — AI serves the visitor
    # until the operator sends their first message or 30s timeout fires.
    current_mode = await db.get_conversation_mode(cid)
    _fresh_guard = await db.get_conversation_simple(cid) or {}
    _silent_reservation = bool(
        (_fresh_guard.get("metadata") or {}).get("announce_pending")
    )
    if current_mode == "human" and not _silent_reservation:
        logger.info(f"[{cid}] Pipeline aborted: conversation taken by agent while AI was processing")
        return ChatResponse(
            conversation_id=cid,
            message="One moment please, connecting you with a specialist...",
            type="queued",
            model_used="none",
        )

    # ── STEP 7.5: Summary → Confirm → CRM → Close ────────────
    # STATE A: collection complete, summary not shown → show template
    # STATE B: summary shown + confirmed + CRM submitted → closing
    # STATE C: summary shown, not confirmed → normal AI response
    from app.models.lead import get_missing_fields as _gmf

    _fl_crm = await lead_service.get_or_create_lead(cid)
    _cc = {
        "visitor_name": getattr(visitor, "name", None),
        "visitor_email": getattr(visitor, "email", None),
        "visitor_phone": getattr(visitor, "phone", None),
    }
    _gmf_missing = _gmf(_fl_crm or {}, _cc)
    _summary_shown = _conv_meta.get("summary_shown_at")
    _is_confirmed = bool(_conv_meta.get("confirmed_at") or _confirmed_this_turn)

    if not _gmf_missing:
        if not _summary_shown:
            from app.ai.templates import build_summary, get_template as _tpl75
            from app.pipeline.corrections import (
                loop_action,
                needs_return_date,
                summary_fingerprint,
            )

            # ALISTAIR: a round trip with no return date rendered a naked
            # "📅 2026-10-01" — no label, nothing to correct, three loops.
            # ASK before rendering; the label in build_summary is only a net.
            _return_asks = int(_conv_meta.get("return_ask_count") or 0)
            if needs_return_date(_fl_crm or {}) and _return_asks < 2:
                _rt_ask = _tpl75("ask_return_date", tunnel, visitor) or (
                    "Round trip it is — when would you fly back?"
                )
                validated_text = _rt_ask
                gen.model_used = "template"
                gen.cost = 0.0
                # State, not just words: the NEXT turn must read a bare
                # "October 20" as the RETURN. Without this the answer to
                # our own question became a new departure date and walked
                # the outbound forward, re-triggering the same question.
                _meta_rt = dict(_conv_meta)
                _meta_rt["awaiting_return_date"] = True
                # Stored so the next turn needs no extra query to resolve
                # a day-only answer ("the 20th") against the departure.
                _meta_rt["awaiting_return_departure"] = (_fl_crm or {}).get("departure_date")
                _meta_rt["return_ask_count"] = _return_asks + 1
                try:
                    await db.update_conversation(cid, {"metadata": _meta_rt})
                    _conv_meta = _meta_rt
                except Exception as e:
                    logger.warning(f"[{cid}] return-ask state failed: {e}")
                logger.info(
                    f"[{cid}] Step 7.5: round trip without a return — "
                    f"asking (#{_return_asks + 1})"
                )
                _summary_text = None
            elif needs_return_date(_fl_crm or {}):
                # Asked twice and still nothing: stop asking, render what we
                # have (labelled) rather than loop a third time.
                logger.info(f"[{cid}] Step 7.5: return still unknown after 2 asks — rendering labelled")
                _summary_text = build_summary(
                    _fl_crm or {}, extra_legs=_conv_meta.get("extra_legs")
                )
            else:
                _summary_text = build_summary(
                    _fl_crm or {}, extra_legs=_conv_meta.get("extra_legs")
                )

            # A wall shown three times is not a conversation (LOOP BREAKER).
            # The count follows the summary's CONTENT: a client who fixes
            # three different things is iterating, not looping, and must
            # never be escalated for it.
            _render_n = int(_conv_meta.get("summary_render_count") or 0)
            if _summary_text:
                _last_fp = _conv_meta.get("summary_fingerprint")
                _fp = summary_fingerprint(_summary_text)
                if _fp != _last_fp:
                    _render_n = 0        # different trip on screen → fresh start
                _render_n += 1           # counts EVERY attempt, including asks
                _meta_count = dict(_conv_meta)
                _meta_count["summary_render_count"] = _render_n
                _meta_count["summary_fingerprint"] = _fp
                try:
                    await db.update_conversation(cid, {"metadata": _meta_count})
                    _conv_meta = _meta_count
                except Exception as e:
                    logger.warning(f"[{cid}] render-count update failed: {e}")
            _loop = loop_action(_render_n) if _summary_text else None
            if _loop:
                _loop_key = (
                    "summary_loop_consultant" if _loop == "consultant"
                    else "summary_loop_escalation"
                )
                validated_text = _tpl75(_loop_key, tunnel, visitor) or (
                    "Let's reset this cleanly — tell me the trip in one line."
                )
                gen.model_used = "template"
                gen.cost = 0.0
                logger.info(
                    f"[{cid}] Step 7.5: summary loop ×{_render_n} — {_loop}"
                )
                if _loop == "consultant":
                    # Stop asking and put a human on it — the same queue
                    # path a "talk to an agent" request takes (#187).
                    from app.services.routing import dispatch_needs_agent

                    try:
                        await db.update_conversation(cid, {"status": "needs_agent"})
                    except Exception as e:
                        logger.warning(f"[{cid}] loop→needs_agent flip failed: {e}")
                    _fire_and_forget(dispatch_needs_agent(cid))
                _summary_text = None

            if _summary_text:
                validated_text = _summary_text
                _meta_upd = dict(_conv_meta)
                _meta_upd["summary_shown_at"] = datetime.now(timezone.utc).isoformat()
                _meta_upd["summary_render_count"] = _render_n + 1
                # Open-door arms on CONFIRMATION now, not here — a post-summary
                # "no" must reach the rejection branch, never the graceful-no
                # list of the open-door capture (the Costa collision).
                await db.update_conversation(cid, {"metadata": _meta_upd})
                _conv_meta = _meta_upd
                gen.model_used = "template"
                gen.cost = 0.0
                logger.info(f"[{cid}] Step 7.5: Summary shown (awaiting confirmation)")

        elif _conv_meta.get("confirmed_at") and (
            _confirmed_this_turn or _crm_submitted_this_turn
        ):
            # The closing no longer waits for a SUCCESSFUL CRM push: a
            # confirmed client whose push failed used to get no closing at
            # all. The push outcome only chooses what we may PROMISE.
            from app.services.closing import (
                claim_closing_sent,
                compute_closing_text,
                generate_closing,
            )

            _claimed = await claim_closing_sent(cid)
            if _claimed:
                _site = metadata.get("site") if metadata else None
                # Deliberately NOT streamed to the client: Step 7.5 REPLACES
                # whatever the main generation produced, and that answer may
                # already have streamed — appending closing chunks on top
                # would render the reply twice. The 6s budget still applies.
                _closing_text, _closing_model, _closing_cost = await generate_closing(
                    _fl_crm or {},
                    visitor,
                    crm_ok=bool(_crm_submitted_this_turn),
                    site=_site,
                )
                if _closing_text:
                    validated_text = _closing_text
                    gen.model_used = _closing_model
                    gen.cost = _closing_cost
                    logger.info(
                        f"[{cid}] Step 7.5: Confirmed → closing generated "
                        f"(model={_closing_model} crm_ok={bool(_crm_submitted_this_turn)})"
                    )
                else:
                    # Warmth failed; the client still gets closed properly.
                    from app.pipeline.generator import _record_generation_fallback

                    validated_text = compute_closing_text(_site)
                    gen.model_used = "template"
                    gen.cost = 0.0
                    _record_generation_fallback(
                        intent, "opus", cid, reason="closing"
                    )
                    logger.info(f"[{cid}] Step 7.5: Closing fell back to template")
            else:
                validated_text = "You're all set! Our consultant will reach out shortly."
                gen.model_used = "template"
                gen.cost = 0.0
                logger.info(f"[{cid}] Step 7.5: Closing already sent → ack only")

    ai_msg = await conversation_service.add_message(
        conversation_id=cid,
        role="ai",
        content=validated_text,
        model_used=gen.model_used,
        cost=gen.cost,
    )
    if ai_msg:
        if _persist_state is not None:
            _persist_state["ai_persisted"] = True
    else:
        logger.error(
            f"[{cid}] AI reply generated but NOT persisted (add_message returned None)"
        )
    ai_msg_id = ai_msg["id"] if ai_msg and isinstance(ai_msg, dict) else None

    if _is_streaming and ai_msg:
        await manager.push_stream_end(cid, ai_msg)

    # ── STEP 8.1: AUTO-CLOSE POST-CRM ────────────────────────
    # Close only after client confirmed summary and CRM was submitted.
    if _crm_submitted_this_turn and _is_confirmed:
        try:
            from app.models.lead import get_missing_fields as _gmf2

            _cc2 = {
                "visitor_name": getattr(visitor, "name", None),
                "visitor_email": getattr(visitor, "email", None),
                "visitor_phone": getattr(visitor, "phone", None),
            }
            if not _gmf2(_fl_crm or {}, _cc2):
                await db.update_conversation(cid, {
                    "status": "completed",
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "ai",
                    "assigned_agent_id": None,
                })
                logger.info(f"[{cid}] Conversation auto-closed post-CRM")
        except Exception as e:
            logger.error(f"[{cid}] Auto-close error: {e}", exc_info=True)

    # Record pipeline run (non-blocking, non-fatal)
    latency_ms = int((time.perf_counter() - pipeline_start) * 1000)
    if ai_msg_id:
        _fire_and_forget(db.create_pipeline_run({
            "message_id": ai_msg_id,
            "conversation_id": cid,
            "step_name": "orchestrator_v1",
            "intent_detected": intent.value,
            "kb_entries_used": len(kb_results),
            "model_used": gen.model_used,
            "cost": gen.cost,
            "latency_ms": latency_ms,
            "status": "success",
            "tunnel": tunnel,
            "had_fallback": gen.model_used == "template" and intent not in (
                Intent.GREETING, Intent.CLOSING, Intent.TALK_TO_AGENT
            ),
        }))

    # ── Auto-summarize every 5 messages (after latency measurement) ──
    async def _run_summary():
        try:
            total_msgs = len(history) + 2
            if total_msgs >= 5 and total_msgs % 5 == 0:
                recent = await db.get_recent_messages(cid, limit=10)
                if recent:
                    msg_text = "\n".join([
                        f"{'Customer' if m.get('role')=='user' else 'Agent'}: {m.get('content','')}"
                        for m in recent[-10:]
                    ])
                    summary_prompt = (
                        "Summarize this business class flight booking conversation in "
                        "exactly 2 sentences. Focus on: route, dates, passenger count, "
                        "budget, and current status (browsing/interested/ready to book)."
                    )
                    from app.ai.claude import call_haiku as _summarize
                    summary_text, sum_cost = await asyncio.to_thread(
                        _summarize, summary_prompt, msg_text
                    )
                    if summary_text:
                        await db.update_conversation(cid, {"summary": summary_text})
                        logger.info(
                            f"[{cid}] Summary updated ({total_msgs} msgs, cost=${sum_cost:.4f})"
                        )
        except Exception as e:
            logger.warning(f"[{cid}] Summary failed (non-fatal): {e}")

    _fire_and_forget(_run_summary())

    resp_type = "template" if gen.model_used == "template" else "ai"
    logger.info(f"[{cid}] [PERF] deliver: {(time.perf_counter() - t_section) * 1000:.0f}ms")
    logger.info(f"[{cid}] [PERF] TOTAL: {latency_ms}ms")
    logger.info(f"[{cid}] Pipeline complete | type={resp_type} | {latency_ms}ms")

    return ChatResponse(
        conversation_id=cid,
        message="" if _is_streaming else validated_text,
        streaming=_is_streaming,
        type=resp_type,
        model_used=gen.model_used,
        cost=gen.cost,
        route_card=gen.route_card,
    )
