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

MAX_LENGTH = 350
EMPTY_FALLBACK = "How can I help you with business class travel today?"
PRICE_REPLACEMENT = "contact our specialists for current pricing"

# Sentence boundary: end punctuation, optional closing quote, then whitespace or EOS.
_SENTENCE_START_RE = re.compile(r'[.!?]+["\']?\s+')


def _sentence_span(text: str, index: int) -> tuple[int, int]:
    """Return [start, end) of the sentence containing `index`."""
    start = 0
    for m in _SENTENCE_START_RE.finditer(text[:index]):
        start = m.end()
    end_m = re.search(r'[.!?]+["\']?', text[index:])
    if end_m:
        end = index + end_m.end()
    else:
        end = len(text)
    return start, end


def _replace_matching_sentences(
    text: str, pattern: re.Pattern, replacement: str
) -> str:
    """Replace every sentence that contains a pattern match — never mid-sentence splice."""
    matches = list(pattern.finditer(text))
    if not matches:
        return text

    spans: list[tuple[int, int]] = []
    for m in matches:
        spans.append(_sentence_span(text, m.start()))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    result = text
    for s, e in reversed(merged):
        left = result[:s].rstrip()
        right = result[e:].lstrip()
        pieces = [p for p in (left, replacement, right) if p]
        result = " ".join(pieces)
    return result


def validate_response(text: str) -> str:
    """Validate and fix AI-generated response before delivery.

    Checks:
    1. Exact prices → replace containing sentence
    2. Competitor names → replace containing sentence
    3. Hallucination phrases → replace containing sentence
    4. Length → truncate at sentence boundary
    5. Empty → fallback
    """
    if not text or not text.strip():
        return EMPTY_FALLBACK

    result = text.strip()

    # 1. Replace exact prices that aren't qualified (whole sentence)
    result = _replace_matching_sentences(result, _EXACT_PRICE_RE, PRICE_REPLACEMENT)

    # 2. Replace competitor names (whole sentence)
    result = _replace_matching_sentences(result, _COMPETITOR_RE, "other services")

    # 3. Replace hallucination phrases (whole sentence)
    if _HALLUCINATION_RE.search(result):
        result = _replace_matching_sentences(
            result, _HALLUCINATION_RE, HALLUCINATION_REPLACEMENT
        )

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

    # 5b. Strip markdown formatting — widget renders plain text
    result = re.sub(r"\*\*(.+?)\*\*", r"\1", result)  # **bold** → bold
    result = re.sub(r"\*(.+?)\*", r"\1", result)  # *italic* → italic
    result = re.sub(r"#{1,3}\s*", "", result)  # ## headers → remove
    result = re.sub(r"^[-•]\s+", "", result, flags=re.MULTILINE)  # bullet points → remove

    # 6. XSS prevention: strip any HTML tags from AI output
    result = re.sub(r"<[^>]+>", "", result)

    return result
