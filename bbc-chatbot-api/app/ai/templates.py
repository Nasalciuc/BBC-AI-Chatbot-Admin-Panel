"""Pre-written template responses — handle 60-70 % of messages at $0 cost."""

import random
from typing import Optional

from app.ai.templates_i18n import CONFIRM_MARKERS, I18N_TEMPLATES, RETURN_CLAUSES, SUMMARY_TEMPLATES
from app.models.chat import VisitorInfo
from app.pipeline.language import current_lang

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
    # The same, outside team hours (app.services.closing.is_within_team_hours):
    # 183 of the 305 "within 30 minutes" the chat ever promised were said at
    # night or on a weekend, US Eastern, with nobody at a desk.
    "specialist_handoff_off_hours:sales": [
        "All set{name_suffix} — I have everything your consultant needs. "
        "They'll reach out first thing when the team is back, with hand-picked options. "
        "We'll contact you at {contact}.",
    ],
    # The client refused calls, texts and email (CodeRabbit #255): no channel is
    # promised, and the way to get the options stays theirs to name.
    "specialist_handoff_no_channel:sales": [
        "All set{name_suffix} — I have everything your consultant needs. "
        f"They'll have hand-picked options ready within {CONSULTANT_SLA_TEXT}. "
        "Since you'd rather not get calls, texts or emails, just tell me here how you'd like them, whenever it suits you.",
    ],
    "specialist_handoff_off_hours_no_channel:sales": [
        "All set{name_suffix} — I have everything your consultant needs. "
        "They'll prepare hand-picked options first thing when the team is back. "
        "Since you'd rather not get calls, texts or emails, just tell me here how you'd like them, whenever it suits you.",
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
    # The same, outside team hours (app.services.closing.is_within_team_hours): nobody is "put on this"
    # at night. Near midnight US Eastern (#3359): "I'm putting a consultant on this to call you … While
    # they get to you", and the client waited for a call.
    "callback_confirmed_off_hours:sales": [
        "Of course — a consultant will call you on {number} first thing when the team is back "
        "(from 9 AM US Eastern).",
    ],
    # A number given in answer to "a text or a quick call?": the reply keeps
    # the channel the client chose. 17 Nov: three clients who had chosen texts
    # (one "can't always take a call") were each told "to call you".
    "number_confirmed_text:sales": [
        "Got it — your consultant will text you on {number}.",
    ],
    # A number from a client who chose WhatsApp (the button after the card, or in words): the consultant
    # writes there, and says so — never "text you" (CodeRabbit #270).
    "number_confirmed_whatsapp:sales": [
        "Got it — your consultant will write to you on WhatsApp at {number}.",
    ],
    "number_confirmed:sales": [
        "Got it — your consultant will reach you on {number}.",
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
    # The example must never contain a date the client did not give us.
    # 18 Aug 2026 this said "e.g. 'returning Nov 17'" to a client flying in
    # December, and he answered "I never said anything about November" — he
    # thought we were inventing his trip. Name the FIELDS he can change; the
    # values are his to supply.
    "summary_reask_2:sales": [
        "Almost there — tell me what to change: the dates, the number of "
        "travellers, or the cabin. Reply YES if it's all correct.",
    ],
    # A round trip whose return date we never captured (Alistair): ASK,
    # never render a date with no label.
    "ask_return_date:sales": [
        "Round trip it is — when would you fly back?",
    ],
    # The client named a FIELD but no value ("change the dates"): ask for
    # that field instead of re-rendering the identical summary.
    "ask_field_dates:sales": [
        "Happy to change the dates — which dates would you like?",
    ],
    "ask_field_route:sales": [
        "Of course — which cities should it be?",
    ],
    "ask_field_passengers:sales": [
        "Sure — how many travelers should it be?",
    ],
    "ask_field_cabin:sales": [
        "Of course — which cabin would you prefer?",
    ],
    # Two dates offered as alternatives — pick nothing, ask which.
    "ask_date_choice:sales": [
        "Both work — which one should I lock in, {option_a} or {option_b}?",
    ],
    # Same summary shown three times without a YES: the wall isn't
    # working. Ask for the whole trip in one line.
    "summary_loop_escalation:sales": [
        "Let's reset this cleanly — tell me the trip in one line, "
        "e.g. 'round trip, Oct 1 to Oct 20, 2 travelers'.",
    ],
    # Reached only when nothing is missing (orchestrator, Step 7.5), so the
    # number is ALWAYS on file. It used to ask for it again, and promise "a
    # minute": 36 times from 26 Aug to 2 Nov 2026 the phone was on file all
    # 36 times, 9 of those clients left right there, and a person answered
    # in 2 of the 36 chats. No question and no time: the client hears what
    # is true.
    "summary_loop_consultant:sales": [
        "Let me get a consultant on this with you — they have your details "
        "and will go over the trip with you.",
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
    # Outside team hours nobody is with a client and nobody calls "shortly": near midnight US Eastern
    # (#3359), the line above was said four times to a client who wanted to book that night, while Alex
    # said the team was offline — "Do you realize you contradict yourself?". No question about the dates
    # either: the client had given them twice.
    "no_agent_available_off_hours:sales": [
        "Our consultants are offline right now — they're back at 9 AM US Eastern, and you're first in "
        "line then. Anything they should know before they reach you? Write it here.",
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

    # Build candidate keys. While the turn speaks Spanish, French or Portuguese
    # (app.pipeline.language), that language's wording comes first; a key it
    # lacks falls back to the English one, as before.
    candidates: list[str] = []
    lang = current_lang.get()
    if lang:
        candidates += [f"{key}:{tunnel}:{lang}", f"{key}:{lang}"]
    if not has_name:
        candidates.append(f"{key}:{tunnel}:anonymous")
    candidates.append(f"{key}:{tunnel}")
    candidates.append(key)

    variants: list[str] | None = None
    for candidate in candidates:
        if candidate in I18N_TEMPLATES:
            variants = I18N_TEMPLATES[candidate]
            break
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


def build_summary(lead: dict, extra_legs: list | None = None) -> str | None:
    """Build confirmation summary from lead, or None if incomplete.

    extra_legs: client-stated legs beyond the main route (MARKY said
    "And return from Paris to Sydney on nov 9" — a third city that is
    neither origin nor destination). They are SHOWN, because a summary
    that quietly drops a leg the client just named is worse than none."""
    from config.settings import settings

    if any(not lead.get(f) for f in _REQUIRED_SUMMARY_FIELDS):
        return None

    departure = lead["departure_date"]
    lang = current_lang.get()
    clauses = RETURN_CLAUSES.get(lang or "")
    if lead.get("return_date"):
        return_clause = (clauses[0].format(date=lead["return_date"]) if clauses
                         else f" — {lead['return_date']} (round trip)")
    elif lead.get("trip_type") == "one_way":
        return_clause = clauses[1] if clauses else " (one-way)"
    elif lead.get("trip_type") == "round_trip" and clauses:
        return_clause = clauses[2]
    elif lead.get("trip_type") == "round_trip":
        # Alistair: round_trip with no return_date used to fall through
        # BOTH branches and render a naked date. The orchestrator now asks
        # for the return before rendering; this label is the safety net if
        # a summary ever reaches here anyway — an unlabelled date is what
        # sent him round the loop three times.
        return_clause = " (round trip — return date pending)"
    else:
        return_clause = ""

    summary = SUMMARY_TEMPLATES.get(lang or "", settings.summary_template).format(
        origin_code=lead["origin_code"],
        destination_code=lead["destination_code"],
        departure=departure,
        return_clause=return_clause,
        passengers=lead["passengers"],
        cabin_class=lead.get("cabin_class", "business"),
    )
    if extra_legs:
        _legs = "\n".join(
            f"➕ {leg.get('from', '?')} → {leg.get('to', '?')}"
            + (f" {leg['date']}" if leg.get("date") else "")
            for leg in extra_legs
        )
        # Inserted before the confirmation question so the client reads
        # the full trip before answering YES.
        _marker = CONFIRM_MARKERS.get(lang or "", "\n\nIs everything correct?")
        if _marker in summary:
            summary = summary.replace(_marker, f"\n{_legs}{_marker}", 1)
        else:
            summary = f"{summary}\n{_legs}"
    return summary
