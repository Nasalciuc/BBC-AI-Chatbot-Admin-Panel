"""Response generation — decision tree: template → Haiku → Sonnet → fallback."""

import logging
from dataclasses import dataclass, field
from typing import Optional

from app.pipeline.intent import Intent
from app.models.chat import VisitorInfo, RouteCard
from app.models.lead import get_missing_fields
from app.models.kb import KBResult
from app.ai.templates import get_template
from app.ai.prompts import build_conversational_prompt
from app.ai.claude import call_haiku, call_sonnet

logger = logging.getLogger(__name__)


@dataclass
class GeneratedResponse:
    text: str
    model_used: str                       # "template", "haiku", "sonnet"
    cost: float = 0.0
    route_card: Optional[RouteCard] = None


def _has_route_data(kb_results: list[KBResult]) -> Optional[KBResult]:
    """Check if any KB result is a route entry (by content heuristic)."""
    route_keywords = ["nonstop", "duration", "airlines", "round trip", "fares typically"]
    for result in kb_results:
        if any(w in result.content.lower() for w in route_keywords):
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
    # 1. Greeting
    if intent == Intent.GREETING:
        text = get_template("welcome", tunnel, visitor)
        if text:
            return GeneratedResponse(text=text, model_used="template")

    # 2. Closing
    if intent == Intent.CLOSING:
        text = get_template("closing", tunnel, visitor)
        if text:
            return GeneratedResponse(text=text, model_used="template")

    # 3. Talk to agent
    if intent == Intent.TALK_TO_AGENT:
        text = get_template("talk_to_agent", tunnel, visitor)
        if text:
            return GeneratedResponse(text=text, model_used="template")

    # 4. Route card (NEW_BOOKING or ROUTE_INFO with KB data)
    if intent in (Intent.NEW_BOOKING, Intent.ROUTE_INFO) and kb_results:
        route_kb = _has_route_data(kb_results)
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
            missing = get_missing_fields(lead)

            if not missing:
                # All fields captured → hand off to specialist
                text = get_template(
                    "specialist_handoff", tunnel, visitor,
                    route=lead.get("route_display", "your route"),
                )
                if text:
                    logger.info("Smart routing: specialist_handoff (lead complete)")
                    return GeneratedResponse(text=text, model_used="template")

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
            elif "travel dates" in missing_str:
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
        text = get_template("ai_fallback", tunnel, visitor) or (
            "Let me connect you with a specialist who can help with that right away."
        )
        return GeneratedResponse(text=text, model_used="template")

    # 5. AI generation
    system_prompt = build_conversational_prompt(
        tunnel=tunnel,
        visitor=visitor,
        lead=lead,
        kb_results=kb_results if kb_results else None,
        history=history if history else None,
    )

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
            return GeneratedResponse(text=ai_text, model_used="sonnet", cost=ai_cost)
    else:
        # 5b. Haiku for standard responses
        logger.info("Using Haiku (standard response)")
        ai_text, ai_cost = call_haiku(system_prompt, entities.get("_raw_message", ""))
        if ai_text:
            return GeneratedResponse(text=ai_text, model_used="haiku", cost=ai_cost)

    # 5c. Fallback
    logger.warning("AI generation failed — using fallback template")
    text = get_template("ai_fallback", tunnel, visitor) or (
        "Let me connect you with a specialist who can help with that right away."
    )
    return GeneratedResponse(text=text, model_used="template")
