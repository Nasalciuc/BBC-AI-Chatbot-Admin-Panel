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


async def process_message(
    conversation_id: Optional[str],
    message: str,
    tunnel: str,
    visitor: VisitorInfo,
    metadata: Optional[dict] = None,
    visitor_id: Optional[str] = None,
) -> ChatResponse:
    """Run the 8-step pipeline. Always returns a response — never crashes."""
    conv = None
    try:
        return await asyncio.wait_for(
            _pipeline(conversation_id, message, tunnel, visitor, metadata, visitor_id=visitor_id),
            timeout=settings.pipeline_timeout,
        )
    except asyncio.TimeoutError:
        logger.error(f"Pipeline timeout ({settings.pipeline_timeout}s)")
        cid = conversation_id or "unknown"
        fallback = get_template("ai_fallback", tunnel, visitor) or (
            "Let me connect you with a specialist right away."
        )
        return ChatResponse(
            conversation_id=cid,
            message=fallback,
            type="template_fallback",
            model_used="template",
        )
    except Exception as e:
        logger.error(f"Pipeline fatal error: {e}", exc_info=True)
        cid = conversation_id or "unknown"
        fallback = get_template("ai_fallback", tunnel, visitor) or (
            "Let me connect you with a specialist right away."
        )
        return ChatResponse(
            conversation_id=cid,
            message=fallback,
            type="template_fallback",
            model_used="template",
        )


async def _pipeline(
    conversation_id: Optional[str],
    message: str,
    tunnel: str,
    visitor: VisitorInfo,
    metadata: Optional[dict],
    visitor_id: Optional[str] = None,
) -> ChatResponse:
    """Internal pipeline implementation with 8 steps."""
    import time
    from app.pipeline.entity_extractor import extract_entities, extract_kb_keywords

    pipeline_start = time.perf_counter()
    _crm_submitted_this_turn = False

    # ── STEP 1: GET/CREATE CONVERSATION ───────────────────────
    conv = await conversation_service.get_or_create_conversation(
        conversation_id=conversation_id,
        tunnel=tunnel,
        visitor=visitor,
        visitor_id=visitor_id,
    )
    if not conv or "id" not in conv:
        raise RuntimeError("Failed to create conversation")

    cid = conv["id"]
    logger.info(f"[{cid}] Pipeline start | tunnel={tunnel}")

    # Save user message immediately (never lose data)
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
    except Exception:
        pass

    # Fetch history early — needed by Steps 3.5, 3.6, and 6
    history = await db.get_recent_messages(cid, limit=10)
    logger.info(f"[{cid}] [PERF] setup: {(time.perf_counter() - pipeline_start) * 1000:.0f}ms")
    t_section = time.perf_counter()

    # ── STEP 2: AGENT CHECK (V3 placeholder) ─────────────────
    # V1: always AI mode. V3 will check agent availability here.

    # ── STEP 3: INTENT DETECTION ─────────────────────────────
    intent = detect_intent(message, metadata)
    logger.info(f"[{cid}] Intent: {intent.value}")

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
        # Current message is the (agent_request_count + 1)th request
        if agent_request_count >= 2:
            try:
                await db.update_conversation(cid, {"status": "needs_agent"})
                logger.info(f"[{cid}] HANDOFF: {agent_request_count + 1} agent requests → status=needs_agent")
            except Exception as e:
                logger.warning(f"[{cid}] Failed to set needs_agent status: {e}")

    logger.info(f"[{cid}] [PERF] intent: {(time.perf_counter() - t_section) * 1000:.0f}ms")
    t_section = time.perf_counter()

    # ── STEP 4: ENTITY EXTRACTION ────────────────────────────
    extracted = extract_entities(message)
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
    }

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
    if has_useful:
        await lead_service.update_lead_from_entities(cid, entities)
        logger.info(f"[{cid}] Entities: {', '.join(k for k, v in entities.items() if v and k != '_raw_message')}")

    logger.info(f"[{cid}] [PERF] extraction: {(time.perf_counter() - t_section) * 1000:.0f}ms")
    t_section = time.perf_counter()

    # ── STEP 4.5: CRM SUBMISSION ─────────────────────────────
    if tunnel == "sales" and settings.crm_api_url and has_useful:
        try:
            _lead = await lead_service.get_or_create_lead(cid)
            if _lead and not _lead.get("created_in_crm"):
                if check_crm_ready(_lead, visitor):
                    _lead_id = _lead["id"]

                    async def _crm_submit_pre():
                        try:
                            _crm = await submit_to_crm(_lead, visitor, cid)
                            if _crm.success:
                                await db.mark_lead_created_in_crm(_lead_id)
                                logger.info(f"[{cid}] CRM submitted (background)")
                        except Exception as err:
                            logger.warning(f"[{cid}] CRM background submit failed: {err}")

                    asyncio.create_task(_crm_submit_pre(), name=f"crm_pre_{cid}")
        except Exception as e:
            logger.error(f"CRM step error (non-blocking): {e}")

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
                        "You can reach us directly at +1 (888) 322-7999 — available 24/7."
                    ),
                    model_used="template",
                )
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
                )
                gen = GeneratedResponse(
                    text=(
                        "I've connected you with a specialist. They have all the "
                        "details from our conversation."
                    ),
                    model_used="handoff",
                )
            elif gen.model_used != "template":
                no_agent = get_template("no_agent_available", tunnel, visitor)
                gen = GeneratedResponse(
                    text=no_agent or (
                        "All specialists are currently busy. "
                        "Please call +1 (888) 322-7999."
                    ),
                    model_used="template",
                )
        except Exception as e:
            logger.warning(f"[{cid}] Handoff routing error: {e}")
            gen = GeneratedResponse(
                text="For immediate assistance, please call +1 (888) 322-7999.",
                model_used="template",
            )

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
            entities[key] = val
            _merged = True

        if _merged:
            await lead_service.update_lead_from_entities(cid, entities)
            logger.info(f"[{cid}] Claude extraction merged: {list(_te.keys())}")

    # ── STEP 6.2: CRM RE-CHECK (on corrected lead) ────────────
    if gen.tool_entities and tunnel == "sales" and settings.crm_api_url:
        try:
            _lead_fresh = await lead_service.get_or_create_lead(cid)
            if _lead_fresh and not _lead_fresh.get("created_in_crm"):
                if check_crm_ready(_lead_fresh, visitor):
                    _crm = await submit_to_crm(_lead_fresh, visitor, cid)
                    if _crm.success:
                        await db.mark_lead_created_in_crm(_lead_fresh["id"])
                        _crm_submitted_this_turn = True
                        logger.info(f"[{cid}] CRM submitted via Claude — handoff after response")
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
    current_mode = await db.get_conversation_mode(cid)
    if current_mode == "human":
        logger.info(f"[{cid}] Pipeline aborted: conversation taken by agent while AI was processing")
        return ChatResponse(
            conversation_id=cid,
            message="One moment please, connecting you with a specialist...",
            type="queued",
            model_used="none",
        )

    ai_msg = await conversation_service.add_message(
        conversation_id=cid,
        role="ai",
        content=validated_text,
        model_used=gen.model_used,
        cost=gen.cost,
    )
    ai_msg_id = ai_msg["id"] if ai_msg and isinstance(ai_msg, dict) else None

    if _is_streaming and ai_msg:
        await manager.push_stream_end(cid, ai_msg)

    # ── STEP 8.1: AUTO-CLOSE POST-CRM ────────────────────────
    if _crm_submitted_this_turn:
        try:
            # 1. Send closing template so client sees confirmation
            _closing_msg = await conversation_service.add_message(
                conversation_id=cid,
                role="ai",
                content=settings.post_crm_closing_message,
                model_used="template",
                cost=0.0,
            )
            if _closing_msg:
                await manager.push(cid, _closing_msg)

            # 2. Close conversation — sales team calls from CRM directly
            await db.update_conversation(cid, {
                "status": "closed",
                "closed_at": datetime.now(timezone.utc).isoformat(),
                "mode": "ai",
                "assigned_agent_id": None,
            })
            logger.info(f"[{cid}] CRM submitted — conversation auto-closed (no handoff)")
        except Exception as e:
            logger.error(f"Post-CRM auto-close error (non-blocking): {e}")
            # Fallback: at minimum close the conversation
            try:
                await db.update_conversation(cid, {
                    "status": "closed",
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "ai",
                    "assigned_agent_id": None,
                })
            except Exception:
                pass

    # Record pipeline run (non-blocking, non-fatal)
    latency_ms = int((time.perf_counter() - pipeline_start) * 1000)
    if ai_msg_id:
        await db.create_pipeline_run({
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
        })

    # ── Auto-summarize every 5 messages (after latency measurement) ──
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
                summary_text, sum_cost = await asyncio.to_thread(_summarize, summary_prompt, msg_text)
                if summary_text:
                    await db.update_conversation(cid, {"summary": summary_text})
                    logger.info(f"[{cid}] Summary updated ({total_msgs} msgs, cost=${sum_cost:.4f})")
    except Exception as e:
        logger.warning(f"[{cid}] Summary failed (non-fatal): {e}")

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
