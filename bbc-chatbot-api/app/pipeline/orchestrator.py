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


def _fire_and_forget(coro):
    """Run coroutine in background without blocking pipeline."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(lambda t: (
        _background_tasks.discard(t),
        t.exception() and logger.warning(f"[bg] task failed: {t.exception()}"),
    ))
    return task


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
    if _conv_meta.get("summary_shown_at") and not _conv_meta.get("confirmed_at"):
        _normalized = message.strip().lower().rstrip(".!")
        # REJECTION comes FIRST — before the confirm listener and before any
        # other post-summary listener. Conv "Costa": the client answered "no"
        # to "Is everything correct?" and no branch existed to hear it.
        _reject_words = {
            "no", "nope", "not right", "not correct", "wrong",
            "incorrect", "change",
        }
        if _normalized in _reject_words:
            from app.ai.templates import get_template as _get_tpl

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
            logger.info(f"[{cid}] Summary REJECTED — asking what to correct")
            return ChatResponse(
                conversation_id=cid,
                message=_correction_text,
                type="template",
                model_used="template",
            )
        _confirm_words = {
            "yes", "correct", "looks good", "confirm", "that's right",
            "da", "yep", "yeah", "ok", "okay", "sure", "perfect",
            "great", "absolutely", "that works", "sounds good",
        }
        if _normalized in _confirm_words:
            intent = Intent.CONFIRMED
            logger.info(f"[{cid}] Intent override → CONFIRMED (summary was shown)")
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
    extracted = extract_entities(message)

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
        "_raw_message": message,
        # name: ALWAYS from visitor form data — never extract from message text
        # extracting name from message causes "looking for" or other text fragments
        # to override the real visitor name submitted in the form
        "name": visitor.name if visitor.name else None,
        "email": extracted.email or (visitor.email if visitor.email else None),
        "phone": extracted.phone or (visitor.phone if visitor.phone else None),
        "origin": extracted.origin_code,
        "destination": extracted.destination_code,
        "passengers": extracted.passengers,
        "cabin_class": extracted.cabin_class,
        "departure_date": extracted.departure_date,
        "return_date": extracted.return_date,
        "trip_type": extracted.trip_type,
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
        for key in [
            "origin",
            "destination",
            "departure_date",
            "return_date",
            "trip_type",
            "passengers",
            "cabin_class",
        ]:
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
                        await db.mark_lead_created_in_crm(_lead_fresh["id"])
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
            from app.ai.templates import build_summary

            _summary_text = build_summary(_fl_crm or {})
            if _summary_text:
                validated_text = _summary_text
                _meta_upd = dict(_conv_meta)
                _meta_upd["summary_shown_at"] = datetime.now(timezone.utc).isoformat()
                # Open-door arms on CONFIRMATION now, not here — a post-summary
                # "no" must reach the rejection branch, never the graceful-no
                # list of the open-door capture (the Costa collision).
                await db.update_conversation(cid, {"metadata": _meta_upd})
                _conv_meta = _meta_upd
                gen.model_used = "template"
                gen.cost = 0.0
                logger.info(f"[{cid}] Step 7.5: Summary shown (awaiting confirmation)")

        elif _conv_meta.get("confirmed_at") and _crm_submitted_this_turn:
            from app.services.closing import compute_closing_text, claim_closing_sent

            _claimed = await claim_closing_sent(cid)
            if _claimed:
                validated_text = compute_closing_text(
                    metadata.get("site") if metadata else None
                )
                gen.model_used = "template"
                gen.cost = 0.0
                logger.info(f"[{cid}] Step 7.5: Confirmed → closing (claimed)")
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
