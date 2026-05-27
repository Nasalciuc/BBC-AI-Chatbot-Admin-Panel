"""Response generation — decision tree: template → Haiku → Sonnet → fallback."""

import logging
from dataclasses import dataclass
from typing import Optional

from app.pipeline.intent import Intent
from app.models.chat import VisitorInfo, RouteCard
from app.models.lead import get_missing_fields
from app.models.kb import KBResult
from app.ai.templates import get_template
from app.ai.prompts import build_conversational_prompt
from app.ai.claude import call_haiku, call_haiku_with_tools, call_sonnet

logger = logging.getLogger(__name__)


@dataclass
class GeneratedResponse:
    text: str
    model_used: str                       # "template", "haiku", "sonnet"
    cost: float = 0.0
    route_card: Optional[RouteCard] = None
    tool_entities: Optional[dict] = None


def _has_route_data(kb_results: list[KBResult], entities: dict | None = None) -> Optional[KBResult]:
    """Check if any KB result is a relevant route entry for the user's query."""
    route_keywords = ["nonstop", "duration", "airlines", "round trip", "fares typically"]
    for result in kb_results:
        if any(w in result.content.lower() for w in route_keywords):
            # Relevance check: if user specified origin/destination,
            # the KB entry must mention them in the correct direction.
            if entities:
                origin = (entities.get("origin") or "").lower()
                destination = (entities.get("destination") or "").lower()
                title_lower = result.title.lower()
                content_lower = result.content.lower()

                # Direction-aware: split KB title by " to "
                title_parts = title_lower.split(" to ")
                if len(title_parts) == 2:
                    from_side, to_side = title_parts
                    if origin and origin not in from_side:
                        continue
                    if destination and destination not in to_side:
                        continue
                elif origin or destination:
                    origin_match = origin and (origin in content_lower or origin in title_lower)
                    dest_match = destination and (destination in content_lower or destination in title_lower)
                    if not origin_match and not dest_match:
                        continue
            return result
    return None


def _build_route_card_from_kb(kb_result: KBResult) -> RouteCard | None:
    """Try to extract route card data from a KB entry. Simple heuristic."""
    content = kb_result.content
    title = kb_result.title

    # Extract origin/destination from title like "NYC to London"
    parts = title.split(" to ")
    if len(parts) != 2:
        return None

    # Try to find price range
    import re
    price_match = re.search(r"\$[\d,]+\s*[-–]\s*\$[\d,]+", content)
    price_range = price_match.group(0) if price_match else "varies"

    # Try to find duration
    dur_match = re.search(r"(\d+-\d+ hours?)", content)
    duration = dur_match.group(1) if dur_match else ""

    # Try to find airlines
    air_match = re.search(r"Airlines?:\s*([^.]+)\.", content)
    airlines = air_match.group(1).strip() if air_match else ""

    # Extract airport codes from content
    codes = re.findall(r"\(([A-Z]{3}(?:/[A-Z]{3})?)\)", content)
    origin = codes[0] if codes else parts[0].strip()
    destination = codes[1] if len(codes) > 1 else parts[1].strip()

    return RouteCard(
        origin=origin,
        destination=destination,
        airlines=airlines,
        duration=duration,
        price_range=price_range,
    )


def generate_response(
    intent: Intent,
    entities: dict,
    kb_results: list[KBResult],
    visitor: VisitorInfo,
    lead: Optional[dict],
    history: list[dict],
    tunnel: str,
    budget_remaining: Optional[float] = None,
    skip_templates: bool = False,
) -> GeneratedResponse:
    """Decision tree for response generation.

    1. GREETING → template ($0)
    2. CLOSING  → template ($0)
    3. TALK_TO_AGENT → template ($0)
    4. NEW_BOOKING / ROUTE_INFO + route KB data → route card + template ($0)
    4.5a. BAGGAGE / PRICE / BOOKING_CHANGE → intent-specific template ($0)
    4.5b. Smart routing: lead has data → ask missing fields in conversation
          order: route → dates → phone → email → name → handoff ($0)
    5. Otherwise:
       a. 5+ user messages OR BOOKING_CHANGE → Sonnet ($0.015)
       b. Else → Haiku ($0.003)
       c. If AI fails → fallback template ($0)
    """
    # Count only real user messages in history (exclude system/agent)
    user_msg_count = sum(1 for m in (history or []) if m.get("role") == "user")

    # If conversation already has user messages, GREETING is impossible
    # Client said "hey" but already started conversation — treat as GENERAL
    if intent == Intent.GREETING and user_msg_count > 1:
        intent = Intent.GENERAL_QUESTION

    # ── AI-FIRST: All messages through Claude (templates = fallback only) ──
    # Skip AI-first for: TALK_TO_AGENT (handoff logic), CLOSING (simple goodbye)
    # skip_templates (bad words): force AI empathetic response, not scripted handoff
    _template_only_intents = (
        {Intent.CLOSING}
        if skip_templates
        else {Intent.TALK_TO_AGENT, Intent.CLOSING}
    )
    if intent not in _template_only_intents:
        # Budget check before AI call
        if budget_remaining is None or budget_remaining > 0:
            try:
                _static, _dynamic = build_conversational_prompt(
                    tunnel=tunnel,
                    visitor=visitor,
                    lead=lead,
                    kb_results=kb_results if kb_results else None,
                    history=history if history else None,
                    entities=entities,
                )
                _system = f"{_static}\n\n{_dynamic}"
                _raw = entities.get("_raw_message", "")
                _user_msgs = [m for m in (history or []) if m.get("role") == "user"]
                _use_sonnet = len(_user_msgs) >= 5 or intent == Intent.BOOKING_CHANGE
                if _use_sonnet and len(_raw) > 1500:
                    _use_sonnet = False

                if _use_sonnet:
                    _ai_text, _ai_cost = call_sonnet(_system, _raw)
                    _tool_entities = None
                else:
                    _ai_text, _ai_cost, _te = call_haiku_with_tools(_system, _raw)
                    _tool_entities = _te if _te else None
                    if (not _ai_text or not str(_ai_text).strip()) and _tool_entities:
                        # Tool call succeeded but no text — re-call without tools for natural response
                        _ai_text, _fallback_cost = call_haiku(_system, _raw)
                        _ai_cost += _fallback_cost
                        if not _ai_text or not str(_ai_text).strip():
                            _ai_text = "Let me find the best options for your trip!"

                if _ai_text:
                    logger.info(f"AI-first response | model={'sonnet' if _use_sonnet else 'haiku'} | intent={intent.value}")
                    return GeneratedResponse(
                        text=_ai_text,
                        model_used="sonnet" if _use_sonnet else "haiku",
                        cost=_ai_cost,
                        tool_entities=_tool_entities,
                    )
                else:
                    logger.warning("AI-first: Claude returned empty — falling through to templates")
            except Exception as e:
                logger.error(f"AI-first error (falling through to templates): {e}")

    # ── TEMPLATE FALLBACK (original logic, unchanged) ────────
    # Templates below activate ONLY if:
    # 1. Intent is TALK_TO_AGENT or CLOSING (skipped AI above)
    # 2. Claude failed/returned empty (fell through)
    # 3. Budget exceeded (skipped AI above)

    # 1. Greeting
    if intent == Intent.GREETING:
        text = get_template("welcome", tunnel, visitor)
        if text:
            return GeneratedResponse(text=text, model_used="template")

    # 1.5 First follow-up: Ask about previous contact (company split policy)
    # This triggers when:
    # - User just replied to the greeting (history has welcome + first user message)
    # - And they're not already providing route/booking data
    # - SALES tunnel only (support has different flow)
    if tunnel == "sales" and user_msg_count == 1 and history:
        # Check if last AI message was a greeting
        ai_msgs = [m for m in history if m.get("role") == "ai"]
        if ai_msgs:
            last_ai_content = ai_msgs[-1].get("content", "").lower()
            is_greeting = any(w in last_ai_content for w in ["welcome", "where are you looking", "tell me"])
            
            # Check if user is already answering with route/booking info
            has_route = bool(entities.get("origin") or entities.get("destination"))
            has_dates = bool(entities.get("departure_date"))
            
            if is_greeting and not has_route and not has_dates:
                text = get_template("ask_previous_contact", tunnel, visitor)
                if text:
                    logger.info(f"[{visitor.name or 'visitor'}] Asking about previous contact (split policy)")
                    return GeneratedResponse(text=text, model_used="template")

    # 2. Closing
    if intent == Intent.CLOSING:
        text = get_template("closing", tunnel, visitor)
        if text:
            return GeneratedResponse(text=text, model_used="template")

    # 3. Talk to agent
    if intent == Intent.TALK_TO_AGENT and not skip_templates:
        text = get_template("talk_to_agent", tunnel, visitor)
        if text and history:
            last_ai = next(
                (m for m in reversed(history[-3:]) if m.get("role") == "ai"),
                None,
            )
            if last_ai and last_ai.get("content", "").strip() == text.strip():
                logger.debug("Template talk_to_agent identical to last — skipping")
                text = None
        if text:
            return GeneratedResponse(text=text, model_used="template")

    # 4. Route card (NEW_BOOKING or ROUTE_INFO with KB data)
    if intent in (Intent.NEW_BOOKING, Intent.ROUTE_INFO) and kb_results:
        route_kb = _has_route_data(kb_results, entities)
        if route_kb:
            route_card = _build_route_card_from_kb(route_kb)
            if route_card:
                text = get_template(
                    "route_card_response",
                    tunnel,
                    visitor,
                    route=f"{route_card.origin} → {route_card.destination}",
                    price_range=route_card.price_range,
                    airlines=route_card.airlines,
                    duration=route_card.duration,
                )
                if text:
                    return GeneratedResponse(
                        text=text,
                        model_used="template",
                        route_card=route_card,
                    )

    # ── 4.5a Intent-specific templates ────────────────────────
    # These intents have good template responses — no need for AI
    if intent == Intent.BAGGAGE_INFO:
        text = get_template("baggage_info", tunnel, visitor)
        if text:
            return GeneratedResponse(text=text, model_used="template")

    if intent == Intent.PRICE_INQUIRY:
        text = get_template("price_inquiry", tunnel, visitor)
        if text:
            return GeneratedResponse(text=text, model_used="template")

    if intent == Intent.BOOKING_CHANGE and tunnel == "support":
        text = get_template("booking_change", tunnel, visitor)
        if text:
            return GeneratedResponse(text=text, model_used="template")

    # ── 4.5a-v2 Support-specific templates (V2) ──────────────
    _support_template_intents = {
        Intent.SEAT_SELECTION: "seat_selection",
        Intent.MEAL_PREFERENCE: "meal_preference",
        Intent.LOUNGE_ACCESS: "lounge_access",
        Intent.CHECK_IN: "check_in",
        Intent.VISA_INFO: "visa_info",
        Intent.TRAVEL_INSURANCE: "travel_insurance",
        Intent.PAYMENT_METHODS: "payment_methods",
        Intent.RECEIPT_REQUEST: "receipt_request",
    }
    if intent in _support_template_intents:
        text = get_template(_support_template_intents[intent], tunnel, visitor)
        if text:
            return GeneratedResponse(text=text, model_used="template")

    # ── 4.5b Smart routing — lead-aware template selection ────
    # SALES tunnel only. Guides conversation to collect missing lead data.
    # Uses CONVERSATION priority order (not scoring priority):
    #   route → dates → phone → email → name
    # Only activates when lead already has SOME data (not on first anonymous msg).
    if tunnel == "sales" and lead and isinstance(lead, dict):
        has_some_data = bool(
            lead.get("origin_code") or lead.get("destination_code")
            or lead.get("visitor_name") or lead.get("visitor_email")
            or lead.get("visitor_phone")
        )

        if has_some_data:
            conv_from_visitor = {
                "visitor_name": visitor.name,
                "visitor_email": visitor.email,
                "visitor_phone": visitor.phone,
            }
            missing = get_missing_fields(lead, conv_from_visitor)

            if not missing and not skip_templates:
                # All fields captured — but don't repeat handoff template
                already_sent = False
                if history:
                    for msg in reversed(history[-5:]):
                        if msg.get("role") == "ai":
                            content = (msg.get("content") or "").lower()
                            if (
                                "specialist" in content
                                and (
                                    "reach out" in content
                                    or "contact you" in content
                                    or "connect" in content
                                )
                            ):
                                already_sent = True
                                break

                if not already_sent:
                    text = get_template(
                        "specialist_handoff", tunnel, visitor,
                        route=lead.get("route_display", "your route"),
                    )
                    if text:
                        logger.info(
                            "Smart routing: specialist_handoff (lead complete, first time)"
                        )
                        return GeneratedResponse(text=text, model_used="template")
                else:
                    logger.debug(
                        "specialist_handoff already sent — AI responds normally"
                    )
                # Fall through to AI-first generation

            # Check what's missing in CONVERSATION order (not scoring order)
            missing_str = " ".join(missing)

            # Loop prevention: if last AI message already asked for this field, skip
            last_ai = ""
            if history:
                ai_msgs = [m for m in history if m.get("role") == "ai"]
                if ai_msgs:
                    last_ai = ai_msgs[-1].get("content", "").lower()

            # Route incomplete → let step 4 (route_card) or AI handle it
            if "route" in missing_str:
                pass  # Don't ask for route via template — too complex

            # Dates missing → ask dates (if we didn't just ask)
            elif (
                "travel dates" in missing_str
                or "departure date" in missing_str
                or "return date" in missing_str
            ):
                if not any(w in last_ai for w in ["when", "dates", "travel", "flexibility"]):
                    # If we have origin+destination, use confirm_route template
                    if lead.get("origin_code") and lead.get("destination_code"):
                        text = get_template(
                            "confirm_route", tunnel, visitor,
                            origin=lead.get("origin_code", ""),
                            destination=lead.get("destination_code", ""),
                        )
                    else:
                        text = get_template("ask_dates", tunnel, visitor)
                    if text:
                        logger.info("Smart routing: ask_dates")
                        return GeneratedResponse(text=text, model_used="template")

            # Phone missing → ask phone (if we didn't just ask)
            elif "phone" in missing_str:
                if "phone" not in last_ai and "number" not in last_ai and "reach" not in last_ai:
                    text = get_template("ask_phone", tunnel, visitor)
                    if text:
                        logger.info("Smart routing: ask_phone")
                        return GeneratedResponse(text=text, model_used="template")

            # Email missing → ask email (if we didn't just ask)
            elif "email" in missing_str:
                if "email" not in last_ai and "e-mail" not in last_ai:
                    text = get_template("ask_email", tunnel, visitor)
                    if text:
                        logger.info("Smart routing: ask_email")
                        return GeneratedResponse(text=text, model_used="template")

            # Name missing → ask name (if we didn't just ask)
            elif "name" in missing_str:
                if "name" not in last_ai:
                    text = get_template("ask_name", tunnel, visitor)
                    if text:
                        logger.info("Smart routing: ask_name")
                        return GeneratedResponse(text=text, model_used="template")

    # ── 4.9 Budget guard ─────────────────────────────────────
    if budget_remaining is not None and budget_remaining <= 0:
        logger.warning(f"Daily budget exceeded (remaining=${budget_remaining:.2f}) — skipping AI")
        fallback_key = "no_agent_available" if skip_templates else "ai_fallback"
        text = get_template(fallback_key, tunnel, visitor) or (
            "For immediate assistance, please call +1 (888) 322-7999."
            if skip_templates
            else "Let me connect you with a specialist who can help with that right away."
        )
        return GeneratedResponse(text=text, model_used="template")

    # 5. AI generation
    _static, _dynamic = build_conversational_prompt(
        tunnel=tunnel,
        visitor=visitor,
        lead=lead,
        kb_results=kb_results if kb_results else None,
        history=history if history else None,
        entities=entities,
    )
    system_prompt = f"{_static}\n\n{_dynamic}"

    user_messages = [m for m in history if m.get("role") == "user"]
    use_sonnet = len(user_messages) >= 5 or intent == Intent.BOOKING_CHANGE

    # Wallet protection: suspicious long messages force Haiku
    if use_sonnet and len(entities.get("_raw_message", "")) > 1500:
        logger.info(f"Wallet protection: long message ({len(entities['_raw_message'])} chars) → forcing Haiku")
        use_sonnet = False

    if use_sonnet:
        # 5a. Sonnet for complex conversations
        logger.info("Using Sonnet (complex conversation)")
        ai_text, ai_cost = call_sonnet(system_prompt, entities.get("_raw_message", ""))
        if ai_text:
            return GeneratedResponse(
                text=ai_text, model_used="sonnet", cost=ai_cost, tool_entities=None
            )
    else:
        # 5b. Haiku for standard responses
        logger.info("Using Haiku (standard response)")
        ai_text, ai_cost, _te = call_haiku_with_tools(
            system_prompt, entities.get("_raw_message", "")
        )
        tool_entities = _te if _te else None
        if (not ai_text or not str(ai_text).strip()) and tool_entities:
            # Tool call succeeded but no text — re-call without tools for natural response
            ai_text, _fallback_cost = call_haiku(system_prompt, entities.get("_raw_message", ""))
            ai_cost += _fallback_cost
            if not ai_text or not str(ai_text).strip():
                ai_text = "Let me find the best options for your trip!"
        if ai_text:
            return GeneratedResponse(
                text=ai_text, model_used="haiku", cost=ai_cost, tool_entities=tool_entities
            )

    # 5c. Fallback
    logger.warning("AI generation failed — using fallback template")
    fallback_key = "no_agent_available" if skip_templates else "ai_fallback"
    text = get_template(fallback_key, tunnel, visitor) or (
        "For immediate assistance, please call +1 (888) 322-7999."
        if skip_templates
        else "Let me connect you with a specialist who can help with that right away."
    )
    return GeneratedResponse(text=text, model_used="template")
