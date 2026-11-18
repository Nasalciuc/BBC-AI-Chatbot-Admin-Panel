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
    # Spanish, French, Portuguese
    r"|ll[aá]m(?:ame|enme|eme)|ll[aá]meme|(?:re)?appelez[- ]moi|pode(?:m)?\s+me\s+ligar|me\s+liga(?:r)?"
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
    r"c[ae]ll\s+me\s+later|maybe\s+later|"
    r"no\s+me\s+ll[aá]m(?:en|e|es)|ne\s+m'appelez\s+pas|n[aã]o\s+me\s+lig(?:ue|uem))\b",
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
# parens and a leading +. It never ends on a lone digit after a gap: in
# "0412 345 678 2 passengers" the 2 is the passengers, not the number's last
# digit (CodeRabbit #270, as entity_extractor.PHONE_RE).
_PHONE_IN_TEXT_RE = re.compile(r"(\+?\d[\d\-.\s()]{6,}\d(?<![-.\s]\d))")


def find_phone(message: str, country: Optional[str] = None) -> Optional[str]:
    """The number the client typed, normalized to E.164 when possible; `country`
    places a number typed the local way (app.services.phone_country)."""
    m = _PHONE_IN_TEXT_RE.search(message or "")
    if not m:
        return None
    from app.services.crm import format_phone_international

    return format_phone_international(m.group(1), country) or None


def detect_callback_request(message: str, country: Optional[str] = None) -> Optional[dict]:
    """{'number': str|None, 'cue': 'words'|'number'} or None.

    A bare phone number counts: post-greeting, a client who types only
    their number is asking to be reached on it.

    A phone types "don’t" (U+2019) by default; read it as "don't", or
    "don’t call me" is heard as "call me" — the opposite of what was said."""
    text = (message or "").replace("’", "'").replace("‘", "'").strip()
    if not text or _NOT_A_REQUEST_RE.search(text):
        return None
    if _CALL_ME_NAME_RE.search(text) and not find_phone(text, country):
        return None                      # "call me Alex" is a name, not a request
    phone = find_phone(text, country)
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
    r"|\b0[1-9](?:[\s.-]?\d){7,11}\b"  # local, with its trunk 0 ("0412 345 678"; #3296)
)
_DATE_SHAPE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/.]\d{1,2}([/.]\d{2,4})?\b"
)


def _is_phone_shaped(text: str) -> bool:
    if _DATE_SHAPE_RE.search(text):
        return False
    return bool(_PHONE_SHAPE_RE.search(text))


_NOTE_CALL = "CLIENT ASKED TO BE CALLED"
_NOTE_TEXT = "CLIENT ASKED FOR TEXTS, NOT CALLS"
_NOTE_ANY = "CLIENT GAVE A NUMBER (no preference stated)"
# The WhatsApp button after the card (app.pipeline.card_buttons): the agent writes there, never a call.
_NOTE_WHATSAPP = "CLIENT ASKED FOR WHATSAPP, NOT CALLS"


def callback_note(number: Optional[str], channel: str = "call") -> str:
    """The line the consultant sees at the top of the lead: how the client
    wants to be reached on the number ('call', 'text', 'whatsapp' — the button
    after the card — or 'any' when they gave the number and said nothing more:
    contact_capture.callback_channel). The panel reads a CLIENT ASKED TO BE
    CALLED line as "Asks: a call back"."""
    shown = number or "number not captured"
    if channel == "text":
        return f"{_NOTE_TEXT} — {shown}"
    if channel == "whatsapp":
        return f"{_NOTE_WHATSAPP} — {shown}"
    if channel == "any":
        return f"{_NOTE_ANY} — {shown}"
    return f"{_NOTE_CALL} — {shown}"


def notes_with_callback(notes: Optional[str], note: str) -> str:
    """The lead's notes with `note` on top and no earlier call-back line: the
    client's newest wish is the one the consultant reads. A client who asked
    for a call and then chose texts must not still show "a call back"."""
    kept = [
        line for line in (notes or "").splitlines()
        if not line.strip().startswith((_NOTE_CALL, _NOTE_TEXT, _NOTE_ANY, _NOTE_WHATSAPP))
    ]
    return "\n".join([note, *kept]).strip()


def notes_without_call(notes: Optional[str], *, texts: bool) -> Optional[str]:
    """The lead's notes once the client turned the call down in a later
    message: the CLIENT ASKED TO BE CALLED line becomes the texts line for the
    same number when they chose texts, and goes when they only said no calls,
    so the panel stops saying "Asks: a call back" (CodeRabbit #262). None when
    there was no such line."""
    out: list[str] = []
    changed = False
    for line in (notes or "").splitlines():
        if not line.strip().startswith(_NOTE_CALL):
            out.append(line)
            continue
        changed = True
        if texts:
            _, dash, number = line.strip().partition("—")
            out.append(callback_note(number.strip() if dash and number.strip() else None, channel="text"))
    return "\n".join(out).strip() if changed else None
