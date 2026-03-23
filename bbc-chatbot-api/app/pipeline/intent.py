"""Intent detection — regex first (free), Claude Haiku fallback (paid)."""

import re
import logging
from enum import Enum
from typing import Optional

from app.ai.claude import classify_intent as claude_classify

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    NEW_BOOKING = "new_booking"
    PRICE_INQUIRY = "price_inquiry"
    ROUTE_INFO = "route_info"
    BOOKING_CHANGE = "booking_change"
    BAGGAGE_INFO = "baggage_info"
    GENERAL_QUESTION = "general_question"
    GREETING = "greeting"
    CLOSING = "closing"
    TALK_TO_AGENT = "talk_to_agent"
    # Support V2
    SEAT_SELECTION = "seat_selection"
    MEAL_PREFERENCE = "meal_preference"
    LOUNGE_ACCESS = "lounge_access"
    CHECK_IN = "check_in"
    VISA_INFO = "visa_info"
    TRAVEL_INSURANCE = "travel_insurance"
    PAYMENT_METHODS = "payment_methods"
    RECEIPT_REQUEST = "receipt_request"
    OTHER = "other"


# Ordered: most specific → most generic
INTENT_PATTERNS: list[tuple[Intent, re.Pattern]] = [
    (Intent.TALK_TO_AGENT, re.compile(r"(talk|speak|connect).*(agent|human|person|someone)", re.I)),
    (Intent.GREETING, re.compile(r"^(hi|hello|hey|good\s+(morning|afternoon|evening)|howdy)\b", re.I)),
    (Intent.CLOSING, re.compile(r"(thank|bye|goodbye|see\s+you|that.?s\s+all)", re.I)),
    (Intent.BOOKING_CHANGE, re.compile(r"(change|cancel|modify|reschedule|refund)", re.I)),
    (Intent.BAGGAGE_INFO, re.compile(r"(baggage|luggage|bag|carry.on|checked|weight)", re.I)),
    (Intent.PRICE_INQUIRY, re.compile(r"(price|cost|how much|rate|fare|cheap|expensive|afford)", re.I)),
    (Intent.SEAT_SELECTION, re.compile(r"(\bseat\b|seating|window\s*seat|aisle\s*seat|seat\s*map|seat\s*select|seat\s*assign|seat\s*prefer)", re.I)),
    (Intent.MEAL_PREFERENCE, re.compile(r"(meal|food|diet|vegetarian|vegan|kosher|halal|gluten|pre.?order)", re.I)),
    (Intent.LOUNGE_ACCESS, re.compile(r"(lounge|priority\s*pass|airport\s*club|vip\s*area)", re.I)),
    (Intent.CHECK_IN, re.compile(r"(check.?in|checking\s+in|check\s+in|boarding\s+pass)", re.I)),
    (Intent.VISA_INFO, re.compile(r"(visa|passport|travel\s*document|entry\s*require)", re.I)),
    (Intent.TRAVEL_INSURANCE, re.compile(r"(insurance|coverage|protect|insured)", re.I)),
    (Intent.PAYMENT_METHODS, re.compile(r"(payment|pay|credit\s*card|wire|transfer|install|invoice)", re.I)),
    (Intent.RECEIPT_REQUEST, re.compile(r"(receipt|invoice|confirmation|proof\s*of)", re.I)),
    (Intent.NEW_BOOKING, re.compile(r"(book|booking|fly(?!\s+direct)|flying|flight|tickets?|travel|trips?|help|want|need)\b", re.I)),
    (Intent.ROUTE_INFO, re.compile(r"(route|airline|nonstop|direct|duration|how\s+long)", re.I)),
]

# Map Claude's response text to our Intent enum
_INTENT_MAP: dict[str, Intent] = {}
for intent in Intent:
    _INTENT_MAP[intent.value] = intent
    _INTENT_MAP[intent.value.upper()] = intent
    _INTENT_MAP[intent.name] = intent
    _INTENT_MAP[intent.name.lower()] = intent


def _parse_claude_intent(raw: str) -> Intent:
    """Fuzzy-match Claude's response to an Intent enum value."""
    cleaned = raw.strip().upper().replace(" ", "_")
    return _INTENT_MAP.get(cleaned, Intent.GENERAL_QUESTION)


def detect_intent(message: str, metadata: Optional[dict] = None) -> Intent:
    """Detect visitor intent from message text.

    1. Check metadata.quick_reply_intent (instant, free)
    2. Try regex patterns (< 5 ms, free)
    3. Fall back to Claude Haiku classification (< 500 ms, paid)
    4. If all fail → GENERAL_QUESTION
    """
    # 1. Quick-reply override
    if metadata and metadata.get("quick_reply_intent"):
        qr = metadata["quick_reply_intent"]
        intent = _INTENT_MAP.get(qr) or _INTENT_MAP.get(qr.upper())
        if intent:
            logger.debug(f"Intent from quick_reply: {intent.value}")
            return intent

    # 2. Regex patterns
    lower = message.lower()
    for intent, pattern in INTENT_PATTERNS:
        if pattern.search(lower):
            logger.debug(f"Intent from regex: {intent.value}")
            return intent

    # 3. Claude Haiku fallback
    logger.debug("No regex match — calling Claude classifier")
    raw, _cls_cost = claude_classify(message)
    if raw:
        intent = _parse_claude_intent(raw)
        logger.info(f"Intent from Claude: {intent.value} (raw={raw!r})")
        return intent

    # 4. Default
    logger.warning("Intent detection failed — defaulting to GENERAL_QUESTION")
    return Intent.GENERAL_QUESTION
