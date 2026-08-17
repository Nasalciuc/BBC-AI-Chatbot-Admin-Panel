"""Pre-written template responses — handle 60-70 % of messages at $0 cost."""

import random
from typing import Optional

from app.models.chat import VisitorInfo

# ── Template registry ─────────────────────────────────────────
# Keys follow the pattern: "{key}:{tunnel}" or "{key}:{tunnel}:anonymous"

# One SLA everywhere the client hears it. CALL PRIMING in the prompt says
# ~30 minutes; templates promising "2 hours" contradicted it.
CONSULTANT_SLA_TEXT = "~30 minutes"

TEMPLATES: dict[str, list[str]] = {
    # ── SALES ──────────────────────────────────────────────────
    "welcome:sales": [
        "Welcome, {name}! Where are you dreaming of flying business class?",
        "Hi {name} — you're in the right place. Where are you flying from and to?",
    ],
    "welcome:sales:anonymous": [
        "Welcome! Where are you dreaming of flying business class?",
        "Hi — you're in the right place. Where are you flying from and to?",
    ],
    "route_card_response:sales": [
        "Private fares on {route} typically run well below public — "
        "your consultant pulls the exact numbers. "
        "When are you looking to travel?",
    ],
    "ask_name:sales": [
        "Let's get this moving — what's your name?",
        "And your name, so your consultant knows who to ask for?",
    ],
    "ask_email:sales": [
        "What's the best email for your options, {name}?",
        "Where should the options land — best email to reach you?",
    ],
    "ask_phone:sales": [
        "Your consultant calls with the hand-picked options — "
        "what's the best number to reach you?",
    ],
    "ask_dates:sales": [
        "When are you looking to travel? Even a day or two of flexibility "
        "often unlocks better fares.",
        "What dates work best for you? Flexible dates tend to open up "
        "the strongest private fares.",
    ],
    "ask_passengers:sales": [
        "How many will be traveling?",
        "Will anyone else be joining you on this trip?",
    ],
    "confirm_route:sales": [
        "{origin} to {destination}, business class. "
        "When are you looking to travel?",
        "{origin} to {destination} in business class. "
        "What dates work for you?",
    ],
    "specialist_handoff:sales": [
        "All set{name_suffix} — I have everything your consultant needs. "
        f"They'll reach out within {CONSULTANT_SLA_TEXT} with hand-picked options. "
        "We'll contact you at {contact}.",
    ],
    "lead_captured:sales": [
        "That's everything, {name} — your consultant will reach out "
        f"within {CONSULTANT_SLA_TEXT} with the best {{route}} options. "
        "We'll contact you at {contact}.",
    ],
    # JOSEF: a client who asks to be called gets a YES, not a question
    # about what he meant.
    "callback_confirmed:sales": [
        "Of course — I'm putting a consultant on this to call you on {number}.",
    ],
    "callback_confirmed_no_number:sales": [
        "Of course — a consultant will call you. "
        "What's the best number to reach you on?",
    ],
    "summary_correction:sales": [
        "Thanks for catching that — what should I fix: "
        "the route, the dates, or the passengers?",
    ],
    "summary_reask:sales": [
        "Just to confirm everything's correct — reply YES, "
        "or tell me what to change.",
    ],
    # Second re-ask in the same summary cycle: teach the format instead of
    # repeating the wall verbatim (Deborah burned two turns on it).
    "summary_reask_2:sales": [
        "Almost there — tell me what to change, e.g. 'returning Nov 17' "
        "or 'make it round trip'.",
    ],
    "closing:sales": [
        "Thanks{name_suffix} — your consultant takes it from here. Speak soon.",
        "Your search is in expert hands{name_suffix}. We'll be in touch soon.",
    ],
    "ask_previous_contact:sales": [
        "Have you contacted us before about business class travel?",
        "Have you worked with us previously on flight bookings?",
        "Is this your first time reaching out to us?",
    ],

    # ── SUPPORT ────────────────────────────────────────────────
    "welcome:support": [
        "Hi{name_suffix}! I can help with booking changes, cancellations, "
        "or questions about your trip.",
    ],
    "welcome:support:anonymous": [
        "Hi! I can help with booking changes, cancellations, "
        "or questions about your trip.",
    ],
    "closing:support": [
        "Glad I could help{name_suffix}! Don't hesitate to reach out "
        "if you need anything else.",
    ],

    # ── INTENT-SPECIFIC ───────────────────────────────────────
    "baggage_info": [
        "Business class typically includes 2 checked bags (32 kg each) "
        "plus a carry-on. Exact allowances vary by airline — "
        "would you like me to check a specific carrier?",
    ],
    "price_inquiry": [
        "I'd be happy to get you a quote! Could you tell me your "
        "departure city, destination, and approximate travel dates?",
    ],
    "booking_change": [
        "I can help with changes to an existing booking. "
        "Could you share your booking reference or confirmation number?",
    ],

    # ── UNIVERSAL ──────────────────────────────────────────────
    "talk_to_agent": [
        "Let me check if a specialist is available for you.",
    ],
    "handoff_confirmed": [
        "Absolutely — I'm connecting you with a specialist right now. "
        "They'll have all the details from our conversation and will "
        "reach out within 30 minutes.",
        "I've flagged this for our team. A specialist will review "
        "everything we've discussed and get back to you shortly.",
    ],
    "after_hours": [
        "Our specialists are available 9 AM – 6 PM EST. "
        "Leave your number and we'll reach out first thing tomorrow.",
    ],
    "no_agent_available": [
        "I apologize \u2014 all our travel specialists are currently assisting "
        "other clients. We have your details and will call you shortly, or you "
        "can reach us directly at +1 (888) 322-7999 \u2014 we're available 24/7.",
    ],
    "ai_fallback": [
        "For the best answer on that, I'd recommend calling our team directly at +1 (888) 322-7999.",
        "That's a great question — our travel specialists can help. Call +1 (888) 322-7999 for immediate assistance.",
    ],
    # Sales NEVER dead-ends to a phone number: a generation failure landed
    # this exact template on live clients mid-purchase (Catherine LAX→SYD
    # giving dates got "recommend calling our team"). The fallback keeps
    # collecting — one question, phone at most secondary. The un-suffixed
    # keys above stay for the support tunnel.
    "ai_fallback:sales": [
        "Your consultant prices that directly — the private fares aren't on "
        "any public site. When are you looking to travel?",
        "That's exactly what your consultant digs into — those fares never "
        "show up on public sites. Which route are you working with?",
    ],
    "no_agent_available:sales": [
        "Every consultant is with a client right now — you're already in "
        "line and they'll call you shortly. To get ahead of it: when are "
        "you looking to travel? If you'd rather talk right away, we're at "
        "+1 (888) 322-7999.",
    ],
    "rate_limited": [
        "I need a moment to process that. For immediate assistance, "
        "please call +1 (888) 322-7999.",
    ],
    "error": [
        "I'm sorry, something went wrong on our end. "
        "Please try again in a moment or call us directly.",
    ],

    # ── NEW TEMPLATES (V2) ─────────────────────────────────────

    "route_info_generic:sales": [
        "Happy to help with that — which cities are you flying between?",
    ],
    "general_question:sales": [
        "Are you looking for routes, pricing, or how the booking works?",
    ],
    "other:sales": [
        "Business class travel is what I do. Are you after flights, routes, or pricing?",
    ],
    "new_booking_no_route:sales": [
        "Let's find the right flight. Where are you flying from and to?",
    ],
    "returning_visitor:sales": [
        "Welcome back, {name} — ready to pick up where we left off?",
    ],
    "first_class_inquiry:sales": [
        "First class — you're doing this right. A few airlines fly their true "
        "flagship cabins, and your consultant hunts for exactly those. "
        "What route are you considering?",
    ],
    "corporate_inquiry:sales": [
        "Consider this handled — groups of 4+ get a dedicated specialist and "
        "priority callback within 1 hour. What route is your team looking at?",
    ],
    "route_info_generic:support": [
        "I can look up route details for you! Could you share your booking reference?",
    ],
    "general_question:support": [
        "I'd be happy to help! Could you give me a few more details about what you need?",
    ],
    "other:support": [
        "I'm here to help with your booking! What do you need assistance with?",
    ],

    # ── SUPPORT V2 TEMPLATES ──────────────────────────────────

    "seat_selection:support": [
        "Seat selection depends on your airline and fare class. "
        "Could you share your booking reference? "
        "I'll check what options are available for your flight.",
    ],
    "meal_preference:support": [
        "Most business class flights offer pre-order meal selection "
        "24-72 hours before departure. Share your booking reference "
        "and I'll check if meal selection is open for your flight.",
    ],
    "lounge_access:support": [
        "Business class tickets include complimentary lounge access "
        "at most major airports. Your boarding pass is all you need. "
        "Would you like to know about a specific airport's lounge?",
    ],
    "check_in:support": [
        "Business class passengers can check in online 24 hours before "
        "departure, or use priority check-in counters at the airport. "
        "Most airlines recommend arriving 3 hours before international flights.",
    ],
    "visa_info:support": [
        "Visa requirements depend on your nationality and destination. "
        "We recommend checking iatatravelcentre.com for the latest requirements. "
        "Would you like help with anything else about your trip?",
    ],
    "travel_insurance:support": [
        "We strongly recommend travel insurance for international business class trips. "
        "Many premium credit cards include coverage. "
        "Would you like a specialist to discuss your options?",
    ],
    "payment_methods:support": [
        "We accept all major credit cards, wire transfers, and FlexPay installments. "
        "Corporate accounts have monthly invoicing available. "
        "Which payment method works best for you?",
    ],
    "receipt_request:support": [
        "I can have our team send a receipt or invoice to your email. "
        "Could you share your booking reference and the email address "
        "for the receipt?",
    ],
}


def get_template(
    key: str,
    tunnel: str = "sales",
    visitor: Optional[VisitorInfo] = None,
    **kwargs: str,
) -> str | None:
    """Look up a template, pick a random variant, and fill placeholders.

    Lookup order:
      1. "{key}:{tunnel}:anonymous"  (if visitor has no name)
      2. "{key}:{tunnel}"
      3. "{key}"
    """
    visitor = visitor or VisitorInfo()
    has_name = bool(visitor.name)

    # Build candidate keys
    candidates: list[str] = []
    if not has_name:
        candidates.append(f"{key}:{tunnel}:anonymous")
    candidates.append(f"{key}:{tunnel}")
    candidates.append(key)

    variants: list[str] | None = None
    for candidate in candidates:
        if candidate in TEMPLATES:
            variants = TEMPLATES[candidate]
            break

    if not variants:
        return None

    text = random.choice(variants)

    # Build replacement map
    replacements = {
        "name": visitor.name or "",
        "name_suffix": f", {visitor.name}" if visitor.name else "",
        "contact": visitor.phone or visitor.email or "the number you provided",
        **kwargs,
    }

    try:
        return text.format(**replacements)
    except KeyError:
        # Missing placeholder — return raw template rather than crash
        return text


# ── Lead confirmation summary (3-gate flow) ───────────────────

_REQUIRED_SUMMARY_FIELDS = (
    "origin_code",
    "destination_code",
    "departure_date",
    "passengers",
    "cabin_class",
)


def build_summary(lead: dict) -> str | None:
    """Build confirmation summary from lead, or None if incomplete."""
    from config.settings import settings

    if any(not lead.get(f) for f in _REQUIRED_SUMMARY_FIELDS):
        return None

    departure = lead["departure_date"]
    if lead.get("return_date"):
        return_clause = f" — {lead['return_date']} (round trip)"
    elif lead.get("trip_type") == "one_way":
        return_clause = " (one-way)"
    else:
        return_clause = ""

    return settings.summary_template.format(
        origin_code=lead["origin_code"],
        destination_code=lead["destination_code"],
        departure=departure,
        return_clause=return_clause,
        passengers=lead["passengers"],
        cabin_class=lead.get("cabin_class", "business"),
    )
