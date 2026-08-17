"""A client asking to be CALLED is not an ambiguous message.

JOSEF typed his phone number and asked us to ring him. The pipeline
treated it as unclassifiable text and asked what he meant — to a man who
had just said, in plain words and with his number attached, exactly what
he wanted. Whatever else happens on that turn, the request is heard, the
number is captured, and a human is queued.
"""

from __future__ import annotations

import re
from typing import Optional

# "call me", "ring me", "riing me" (Josef's typo), "give me a call",
# "phone me", "call me back". Deliberately narrow: it must be a request
# aimed at US, not "I'll call you later" or "the airline called me".
_CALL_CUE_RE = re.compile(
    r"\b(?:"
    r"c[ae]ll\s+me(?:\s+back)?"
    r"|ri+ng\s+me"
    r"|phone\s+me"
    r"|give\s+me\s+a\s+(?:call|ring|buzz)"
    r"|(?:can|could|please|pls)\s+(?:you\s+)?(?:c[ae]ll|ri+ng|phone)"
    r"|call\s+back"
    r"|be\s+c[ae]lled"
    r")\b",
    re.IGNORECASE,
)
# "I'll call you", "the airline called me", "no need to call" — and every
# other way of saying the opposite. A REFUSAL read as a request queues a
# human at the client who just said they don't want one.
_NOT_A_REQUEST_RE = re.compile(
    r"\b(?:i(?:'|’)?ll\s+c[ae]ll|i\s+will\s+c[ae]ll|i\s+c[ae]lled|"
    r"they\s+c[ae]lled|already\s+c[ae]lled|"
    r"(?:don'?t|do\s+not|dont|please\s+don'?t|no\s+need\s+to|rather\s+not|"
    r"prefer\s+not\s+to|no)\s+(?:\w+\s+){0,2}?c[ae]ll(?:ed|ing)?\b|"
    r"c[ae]ll\s+me\s+later|maybe\s+later)\b",
    re.IGNORECASE,
)
# "call me Alex" / "you can call me Mr Smith" — an introduction, not a
# request. A NAME follows; a callback request is followed by a number,
# "back", "on", "at", or nothing at all.
_CALL_ME_NAME_RE = re.compile(
    r"\bc[ae]ll\s+me\s+(?!back\b|on\b|at\b|about\b|when\b|asap\b|now\b|today\b|tomorrow\b|please\b|in\b)"
    r"[A-Z][a-z]{1,}",
)
# A phone number in free text: 7+ digits allowing spaces, dots, dashes,
# parens and a leading +.
_PHONE_IN_TEXT_RE = re.compile(r"(\+?\d[\d\-.\s()]{6,}\d)")


def find_phone(message: str) -> Optional[str]:
    """The number the client typed, normalized to E.164 when possible."""
    m = _PHONE_IN_TEXT_RE.search(message or "")
    if not m:
        return None
    from app.services.crm import format_phone_international

    return format_phone_international(m.group(1)) or None


def detect_callback_request(message: str) -> Optional[dict]:
    """{'number': str|None, 'cue': 'words'|'number'} or None.

    A bare phone number counts: post-greeting, a client who types only
    their number is asking to be reached on it."""
    text = (message or "").strip()
    if not text or _NOT_A_REQUEST_RE.search(text):
        return None
    if _CALL_ME_NAME_RE.search(text) and not find_phone(text):
        return None                      # "call me Alex" is a name, not a request
    phone = find_phone(text)
    if _CALL_CUE_RE.search(text):
        return {"number": phone, "cue": "words"}
    if phone and len(text.split()) <= 6 and _is_phone_shaped(text):
        # "+1 210 555 0100" / "my number is 210-555-0100" — the number IS
        # the message. Inside a longer sentence it is just contact data
        # the normal extractor already captures.
        return {"number": phone, "cue": "number"}
    return None


# A booking reference, an order id or a date is not a phone number. A bare
# number only counts when it LOOKS like one: 10+ digits, or a leading +,
# or written in phone groups.
_PHONE_SHAPE_RE = re.compile(
    r"\+\d[\d\s().-]{7,}"
    r"|\b\d{3}[\s.-]\d{3}[\s.-]\d{4}\b"
    r"|\(\d{3}\)\s*\d{3}[\s.-]?\d{4}"
    r"|\b\d{10,}\b"
)
_DATE_SHAPE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/.]\d{1,2}([/.]\d{2,4})?\b"
)


def _is_phone_shaped(text: str) -> bool:
    if _DATE_SHAPE_RE.search(text):
        return False
    return bool(_PHONE_SHAPE_RE.search(text))


def callback_note(number: Optional[str]) -> str:
    """The line the consultant sees at the top of the lead."""
    return f"CLIENT ASKED TO BE CALLED — {number or 'number not captured'}"
