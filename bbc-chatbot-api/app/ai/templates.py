"""Pre-written template responses — handle 60-70 % of messages at $0 cost."""

import random
from typing import Optional

from app.models.chat import VisitorInfo

# ── Template registry ─────────────────────────────────────────
# Keys follow the pattern: "{key}:{tunnel}" or "{key}:{tunnel}:anonymous"

TEMPLATES: dict[str, list[str]] = {
    # ── SALES ──────────────────────────────────────────────────
    "welcome:sales": [
        "Welcome, {name}! Where are you looking to fly in business class?",
        "Hi {name}! Tell me your route and preferred dates, and I'll find options for you.",
    ],
    "welcome:sales:anonymous": [
        "Welcome! Where are you looking to fly in business class?",
        "Hi! Tell me your route and preferred travel dates.",
    ],
    "route_card_response:sales": [
        "Great choice! {route} typically runs {price_range} in business class, "
        "with {airlines} offering service at about {duration}. "
        "Shall I have a specialist find the best fare for your dates?",
    ],
    "ask_name:sales": [
        "I'd love to help you find the best fare! What's your name?",
        "Sure thing! May I have your name so I can look into that?",
    ],
    "ask_email:sales": [
        "What's the best e-mail to send your quote to, {name}?",
        "Could I get your e-mail address so we can send over the options?",
    ],
    "ask_phone:sales": [
        "To get you the best available fare, what's the best number "
        "for our specialist to reach you?",
    ],
    "ask_dates:sales": [
        "When are you looking to travel? Even a few days of flexibility "
        "can help us find significantly better fares.",
        "What dates work best for you? Flexible dates often unlock the best deals.",
    ],
    "ask_passengers:sales": [
        "How many passengers will be flying?",
        "Will anyone else be joining you on this trip?",
    ],
    "confirm_route:sales": [
        "Great — {origin} to {destination} in business class! "
        "When are you looking to travel?",
        "{origin} to {destination} — excellent route! "
        "What dates work for you?",
    ],
    "specialist_handoff:sales": [
        "Wonderful{name_suffix}! I have everything I need. One of our travel "
        "specialists will reach out within 2 hours with the best options. "
        "We'll contact you at {contact}.",
        "All set{name_suffix}! A specialist will contact you shortly with "
        "personalized options and our best fares.",
        "Thank you{name_suffix}! Our team will prepare a tailored quote "
        "and reach out to you soon at {contact}.",
    ],
    "lead_captured:sales": [
        "Perfect, {name}! One of our travel specialists will reach out "
        "within 2 hours with the best {route} options. "
        "We'll contact you at {contact}.",
    ],
    "closing:sales": [
        "Thank you for choosing Buy Business Class{name_suffix}! "
        "We'll be in touch soon.",
        "Thanks{name_suffix}! Our team is already working on your request.",
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
        "I'll connect you with a travel specialist right away. "
        "One moment please.",
    ],
    "after_hours": [
        "Our specialists are available 9 AM – 6 PM EST. "
        "Leave your number and we'll reach out first thing tomorrow.",
    ],
    "ai_fallback": [
        "Let me connect you with a specialist who can help with that right away.",
        "I want to make sure you get the best answer — let me connect you with our team.",
        "That's a great question for our travel specialists. Let me get someone for you.",
    ],
    "rate_limited": [
        "You've been chatting with us a lot! For the fastest service, "
        "call us at +1-XXX-XXX-XXXX.",
    ],
    "error": [
        "I'm sorry, something went wrong on our end. "
        "Please try again in a moment or call us directly.",
    ],

    # ── NEW TEMPLATES (V2) ─────────────────────────────────────

    "route_info_generic:sales": [
        "I'd love to help with route information! Which cities are you flying between?",
    ],
    "general_question:sales": [
        "Great question! Are you looking for information about routes, pricing, or the booking process?",
    ],
    "other:sales": [
        "I specialize in premium business class travel! Are you looking for flights, routes, or pricing?",
    ],
    "new_booking_no_route:sales": [
        "Exciting — let's find you the perfect flight! Where are you departing from, and where would you like to go?",
    ],
    "returning_visitor:sales": [
        "Welcome back, {name}! Ready to continue planning your trip?",
    ],
    "first_class_inquiry:sales": [
        "We handle first class as well! First class typically runs 60-120% more than business. Want me to check availability for your route?",
    ],
    "corporate_inquiry:sales": [
        "Groups of 4+ get additional 10-20% discounts plus a dedicated specialist with priority callback within 1 hour. What route is your team looking at?",
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
