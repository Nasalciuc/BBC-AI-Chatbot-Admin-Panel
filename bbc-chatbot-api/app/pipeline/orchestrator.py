"""8-step pipeline orchestrator — THE BRAIN of the chatbot."""

import asyncio
import logging
from typing import Optional

from config.settings import settings
from app.models.chat import ChatResponse, VisitorInfo
from app.models.kb import KBResult
from app.services import conversation_service, lead_service
from app.db import supabase as db
from app.pipeline.intent import detect_intent, Intent
from app.pipeline.generator import generate_response
from app.pipeline.validator import validate_response
from app.ai.templates import get_template

logger = logging.getLogger(__name__)


async def process_message(
    conversation_id: Optional[str],
    message: str,
    tunnel: str,
    visitor: VisitorInfo,
    metadata: Optional[dict] = None,
) -> ChatResponse:
    """Run the 8-step pipeline. Always returns a response — never crashes."""
    conv = None
    try:
        return await asyncio.wait_for(
            _pipeline(conversation_id, message, tunnel, visitor, metadata),
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
) -> ChatResponse:
    """Internal pipeline implementation with 8 steps."""
    import time
    from app.pipeline.entity_extractor import extract_entities, extract_kb_keywords

    pipeline_start = time.perf_counter()

    # ── STEP 1: GET/CREATE CONVERSATION ───────────────────────
    conv = await conversation_service.get_or_create_conversation(
        conversation_id=conversation_id,
        tunnel=tunnel,
        visitor=visitor,
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

    # Fetch history early — needed by Steps 3.5, 3.6, and 6
    history = await db.get_recent_messages(cid, limit=10)

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
    }

    # Update lead with all extracted entities
    has_useful = any(entities.get(k) for k in ["name", "email", "phone", "origin", "destination", "departure_date"])
    if has_useful:
        await lead_service.update_lead_from_entities(cid, entities)
        logger.info(f"[{cid}] Entities: {', '.join(k for k, v in entities.items() if v and k != '_raw_message')}")

    # ── STEP 5: KB SEARCH ────────────────────────────────────
    kb_results: list[KBResult] = []
    skip_kb = intent in (Intent.GREETING, Intent.CLOSING, Intent.TALK_TO_AGENT)

    if not skip_kb:
        # 5a. Qdrant vector search (server-side embedding)
        if settings.qdrant_enabled and settings.qdrant_url:
            try:
                from app.db.qdrant import search_kb as qdrant_search_kb
                raw_vector = await qdrant_search_kb(message, tunnel=tunnel, limit=3)
                if raw_vector:
                    kb_results = [
                        KBResult(
                            entry_id=r["id"],
                            title=r["title"],
                            content=r["content"],
                            score=r.get("score", 0.8),
                            source="vector",
                        )
                        for r in raw_vector
                    ]
                    logger.info(f"[{cid}] Qdrant results: {len(kb_results)}")
            except Exception as e:
                logger.warning(f"[{cid}] Qdrant failed, keyword fallback: {e}")
                kb_results = []

        # 5b. Keyword fallback
        if not kb_results:
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

    # ── STEP 6: GENERATE RESPONSE ────────────────────────────
    # history already fetched at line 92 — reuse (saves ~400ms roundtrip)
    lead = await lead_service.get_or_create_lead(cid)

    today_cost = await db.get_today_cost()
    budget_remaining = settings.daily_budget - today_cost

    gen = generate_response(
        intent=intent,
        entities=entities,
        kb_results=kb_results,
        visitor=visitor,
        lead=lead,
        history=history,
        tunnel=tunnel,
        budget_remaining=budget_remaining,
    )
    logger.info(f"[{cid}] Generated via {gen.model_used} | cost=${gen.cost:.4f}")

    # ── STEP 7: VALIDATE OUTPUT ──────────────────────────────
    # Skip validation for template responses (trusted content).
    # Only validate AI-generated text (Haiku/Sonnet).
    if gen.model_used == "template":
        validated_text = gen.text
    else:
        validated_text = validate_response(gen.text)

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
        from app.models.chat import ChatResponse
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

    # ── Auto-summarize every 5 messages ──────────────────────
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
                summary_text, sum_cost = _summarize(summary_prompt, msg_text)
                if summary_text:
                    await db.update_conversation(cid, {"summary": summary_text})
                    logger.info(f"[{cid}] Summary updated ({total_msgs} msgs, cost=${sum_cost:.4f})")
    except Exception as e:
        logger.warning(f"[{cid}] Summary failed (non-fatal): {e}")

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

    resp_type = "template" if gen.model_used == "template" else "ai"
    logger.info(f"[{cid}] Pipeline complete | type={resp_type} | {latency_ms}ms")

    return ChatResponse(
        conversation_id=cid,
        message=validated_text,
        type=resp_type,
        model_used=gen.model_used,
        cost=gen.cost,
        route_card=gen.route_card,
    )
