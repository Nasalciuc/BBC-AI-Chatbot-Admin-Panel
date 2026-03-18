"""Output validation — fix AI responses BEFORE sending to visitor."""

import re

# ── Competitor names ──────────────────────────────────────────
COMPETITORS = [
    "kayak", "expedia", "google flights", "skyscanner", "momondo",
    "cheapoair", "priceline", "hopper", "kiwi",
]
_COMPETITOR_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in COMPETITORS) + r")\b",
    re.IGNORECASE,
)

# ── Exact price (not preceded by qualifying words) ────────────
# Matches $1,234 or $1,234.56 NOT preceded by "typically", "around", "range"
_EXACT_PRICE_RE = re.compile(
    r"(?<!\btypically\s)(?<!\baround\s)(?<!\brange\s)"
    r"\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?\b"
)

# ── Hallucination indicators ─────────────────────────────────
HALLUCINATION_PHRASES = [
    "I can confirm your booking",
    "your reservation number is",
    "your booking reference is",
    "I've booked",
    "booking confirmed",
    "reservation confirmed",
]
_HALLUCINATION_RE = re.compile(
    "|".join(re.escape(p) for p in HALLUCINATION_PHRASES),
    re.IGNORECASE,
)

HALLUCINATION_REPLACEMENT = (
    "Let me connect you with a specialist who can confirm those details."
)

MAX_LENGTH = 500
EMPTY_FALLBACK = "How can I help you with business class travel today?"
PRICE_REPLACEMENT = "contact our specialists for current pricing"


def validate_response(text: str) -> str:
    """Validate and fix AI-generated response before delivery.

    Checks:
    1. Exact prices → replace
    2. Competitor names → replace with "other services"
    3. Hallucination phrases → replace with specialist redirect
    4. Length → truncate at sentence boundary
    5. Empty → fallback
    """
    if not text or not text.strip():
        return EMPTY_FALLBACK

    result = text.strip()

    # 1. Replace exact prices that aren't qualified
    result = _EXACT_PRICE_RE.sub(PRICE_REPLACEMENT, result)

    # 2. Replace competitor names
    result = _COMPETITOR_RE.sub("other services", result)

    # 3. Replace hallucination phrases
    if _HALLUCINATION_RE.search(result):
        result = _HALLUCINATION_RE.sub(HALLUCINATION_REPLACEMENT, result)

    # 4. Truncate at sentence boundary if too long
    if len(result) > MAX_LENGTH:
        truncated = result[:MAX_LENGTH]
        # Find last sentence-ending punctuation
        last_period = max(
            truncated.rfind("."),
            truncated.rfind("!"),
            truncated.rfind("?"),
        )
        if last_period > 100:  # Don't truncate to something too short
            result = truncated[: last_period + 1]
        else:
            result = truncated.rstrip() + "…"

    # 5. Empty check (after all processing)
    if not result.strip():
        return EMPTY_FALLBACK

    # 6. XSS prevention: strip any HTML tags from AI output
    result = re.sub(r"<[^>]+>", "", result)

    return result
