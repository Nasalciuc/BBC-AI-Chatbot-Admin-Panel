"""Output validation — fix AI responses BEFORE sending to visitor."""

import re

# ── Competitor names ──────────────────────────────────────────
# Kayak is not one: it is where the clients come from (owner, 17 Nov 2026) —
# the Kayak card opens the chat and Alex may say "the Kayak link".
COMPETITORS = [
    "expedia", "google flights", "skyscanner", "momondo",
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

# Where the next sentence starts: end punctuation, any closing quotes or
# brackets, then whitespace — the same closers _SENTENCE_END_RE accepts, so
# "(local time.) Expedia…" drops only the Expedia sentence (CodeRabbit #263).
_SENTENCE_START_RE = re.compile(r"[.!?]+[\"'”’)\]]*\s+")
# Where a sentence really ends: the punctuation is followed by whitespace or
# the end of the text. The dot inside "ann@mail.com", "1.5" or "+1.210" is no
# end — the old cut took the last dot it found and could stop in the middle
# of an email or a number.
_SENTENCE_END_RE = re.compile(r"[.!?]+[\"'”’)\]]*(?=\s|$)")


def _sentence_span(text: str, index: int) -> tuple[int, int]:
    """Return [start, end) of the sentence containing `index`."""
    start = 0
    for m in _SENTENCE_START_RE.finditer(text[:index]):
        start = m.end()
    # The sentence's real end — not the dot in "$2,800.00" or "expedia.com",
    # which used to leave "00 per person." or "com, but…" behind.
    end_m = _SENTENCE_END_RE.search(text[index:])
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


def _sentences(text: str) -> list[tuple[str, str]]:
    """(whitespace before, sentence) for each sentence of the text, so the
    text can be put back together with its own line breaks."""
    parts: list[tuple[str, str]] = []
    start = 0
    for m in _SENTENCE_END_RE.finditer(text):
        chunk = text[start:m.end()]
        if chunk.strip():
            body = chunk.lstrip()
            parts.append((chunk[: len(chunk) - len(body)], body))
        start = m.end()
    tail = text[start:]
    if tail.strip():
        body = tail.lstrip()
        parts.append((tail[: len(tail) - len(body)], body.rstrip()))
    return parts


def _render(parts: list[tuple[str, str]], keep: list[int]) -> str:
    """The kept sentences, in order. Between two of them goes the strongest
    break of what lay between them in the reply — a paragraph break stays a
    paragraph break even when the sentence that carried it was dropped."""
    out = parts[keep[0]][1] if keep else ""
    for a, b in zip(keep, keep[1:]):
        gap = [parts[k][0] for k in range(a + 1, b + 1)]
        sep = "\n\n" if any("\n\n" in s for s in gap) else "\n" if any("\n" in s for s in gap) else " "
        out += sep + parts[b][1]
    return out


def _shorten(text: str, limit: int = MAX_LENGTH) -> str:
    """Whole sentences within `limit`, and the closing question kept.

    The question is what moves the chat on: 17 Nov, a reply cut to its first
    350 characters ended "…Text to <the number>." and the client was never
    asked the question that followed. So the reply keeps its opening
    sentence, the question, and the sentences right before the question
    (what the question is about); the middle goes first. A single sentence
    too long to keep is cut between two words, never inside one.
    """
    if len(text) <= limit:
        return text
    parts = _sentences(text)
    last = len(parts) - 1
    if last > 0 and parts[last][1].rstrip("\"'”’)]").endswith("?") and len(parts[last][1]) <= limit:
        head = [0] if len(_render(parts, [0, last])) <= limit else []
        tail: list[int] = []
        for k in range(last - 1, len(head) - 1, -1):
            if len(_render(parts, head + [k] + tail + [last])) > limit:
                break
            tail.insert(0, k)
        return _render(parts, head + tail + [last])
    kept: list[int] = []
    for k in range(len(parts)):
        if len(_render(parts, kept + [k])) > limit:
            break
        kept.append(k)
    if kept:
        return _render(parts, kept)
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    # Any space will do, however early: a cut inside a word can leave half a
    # web address or number (CodeRabbit #263). Only text with no space at all
    # is cut where it stands.
    return (cut[:space] if space > 0 else cut).rstrip(" ,;:—-") + "…"


def validate_response(text: str) -> str:
    """Validate and fix AI-generated response before delivery.

    Checks:
    1. Exact prices → replace containing sentence
    2. Competitor names → drop containing sentence
    3. Hallucination phrases → replace containing sentence
    4-5. Markdown and HTML → stripped
    6. Length → whole sentences, the closing question kept
    7. Empty → fallback
    """
    if not text or not text.strip():
        return EMPTY_FALLBACK

    result = text.strip()

    # 1. Replace exact prices that aren't qualified (whole sentence)
    result = _replace_matching_sentences(result, _EXACT_PRICE_RE, PRICE_REPLACEMENT)

    # 2. Drop a sentence that names a competitor. It used to become the words
    # "other services", spliced in mid-reply with no verb around them.
    result = _replace_matching_sentences(result, _COMPETITOR_RE, "")

    # 3. Replace hallucination phrases (whole sentence)
    if _HALLUCINATION_RE.search(result):
        result = _replace_matching_sentences(
            result, _HALLUCINATION_RE, HALLUCINATION_REPLACEMENT
        )

    # 4. Strip markdown formatting — widget renders plain text. Before the
    # length check, so the cut measures what the client reads and a closing
    # "**When do you fly?**" is still seen as the question (CodeRabbit #263).
    result = re.sub(r"\*\*(.+?)\*\*", r"\1", result)  # **bold** → bold
    result = re.sub(r"\*(.+?)\*", r"\1", result)  # *italic* → italic
    result = re.sub(r"#{1,3}\s*", "", result)  # ## headers → remove
    result = re.sub(r"^[-•]\s+", "", result, flags=re.MULTILINE)  # bullet points → remove

    # 5. XSS prevention: strip any HTML tags from AI output
    result = re.sub(r"<[^>]+>", "", result).strip()

    # 6. Too long: whole sentences, and the closing question kept
    result = _shorten(result)

    # 7. Empty check (after all processing)
    if not result.strip():
        return EMPTY_FALLBACK

    return result
