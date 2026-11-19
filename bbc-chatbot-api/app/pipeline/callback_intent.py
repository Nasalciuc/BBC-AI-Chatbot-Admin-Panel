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


# A date written with dots or slashes is no number to call: "call me on 01.12.2026" (CodeRabbit #270).
_DATE_CANDIDATE_RE = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}")


def _is_compact_date(digits: str) -> bool:
    """"20261201", "01 12 2026": eight digits that read as a day (year first or last) are a date."""
    if len(digits) != 8:
        return False

    def _day(y: str, m: str, d: str) -> bool:
        return 1900 <= int(y) <= 2099 and 1 <= int(m) <= 12 and 1 <= int(d) <= 31

    return (_day(digits[:4], digits[4:6], digits[6:]) or _day(digits[4:], digits[2:4], digits[:2])
            or _day(digits[4:], digits[:2], digits[2:4]))


def _candidate_matches(message: str) -> list[re.Match]:
    """The phone-shaped runs of digits in the message, dates left out."""
    return [m for m in _PHONE_IN_TEXT_RE.finditer(message or "")
            if not _DATE_CANDIDATE_RE.fullmatch(m.group(1).strip())
            and not _is_compact_date(re.sub(r"\D", "", m.group(1)))]


def _candidates(message: str) -> list[str]:
    return [m.group(1) for m in _candidate_matches(message)]


def has_number(message: str) -> bool:
    """The message holds a phone-shaped run of digits (no date): worth reading the visit's country for."""
    return bool(_candidates(message))


def find_phone(message: str, country: Optional[str] = None) -> Optional[str]:
    """The number the client typed, normalized to E.164 when possible; `country`
    places a number typed the local way (app.services.phone_country)."""
    found = _candidates(message)
    if not found:
        return None
    from app.services.crm import format_phone_international

    return format_phone_international(found[0], country) or None


def number_to_check(message: str) -> Optional[str]:
    """The number as typed, for the agent to check, when it can't be placed: a local number with no
    country to go with it, or one its country reads two ways. Never dialled as a guess (ADR-6f)."""
    found = _candidates(message)
    if not found:
        return None
    digits_and_spaces = re.sub(r"[^\d ]", "", found[0]).strip()
    return digits_and_spaces if len(re.sub(r"\D", "", digits_and_spaces)) >= 7 else None


# A booking or order reference is no phone, whatever its length: the word stands just before the digits
# ("ref 123456789", "booking number is 912345678", "PNR: …").
_REFERENCE_BEFORE_RE = re.compile(
    r"\b(?:ref(?:erence)?|booking|reservation|order|confirmation|pnr|locator|ticket|invoice|account|member"
    r"|membership|passport|card|id|reserva|referencia|r[ée]f[ée]rence|r[ée]servation|commande|pedido"
    r"|bilhete|localizador|cuenta|conta|compte)\b(?:\W+\w+){0,2}\W*$", re.I)


# "a ticket question, call 2105550100": a phone word between them makes the digits a number again.
_PHONE_WORD_RE = re.compile(
    r"\b(?:phone|call|cell|mobile|whats\s?app|tel|text|reach|contact|telefone|tel[ée]fono|m[óo]vil|portable"
    r"|celular|ligar|ligue|llam\w*|appel\w*)\b", re.I)


def follows_reference(message: str, start: int) -> bool:
    """The digits at `start` come just after a reference word: a booking, an order, an id — no phone."""
    m = _REFERENCE_BEFORE_RE.search((message or "")[:start])
    return bool(m) and not _PHONE_WORD_RE.search(m.group(0))


def national_candidate(message: str, country: Optional[str]) -> Optional[str]:
    """A number typed the national way without its trunk 0 ("912345678" from Portugal), as typed, when its
    length is the visit's country's (app.services.phone_country.NATIONAL_LENGTHS); None for a date, a
    reference, an amount, or a length the country's numbers don't have (CodeRabbit #270). Kept as typed:
    one the country reads two ways ("49301234" in Germany) is then kept for the agent to check
    (lead_service), never guessed.

    Eight digits at least: the national forms without a trunk 0 (Portugal, Spain, Norway, Denmark…) all
    have that many, and a shorter run is far more often a count, a code or an amount."""
    from app.services.phone_country import NATIONAL_LENGTHS, calling_code

    code = calling_code(country)
    lengths = NATIONAL_LENGTHS.get(code or "")
    if not lengths or code == "1":
        return None
    from app.ai.output_guard import amount_spans

    lo, hi = max(lengths[0], 8), lengths[1]
    money = amount_spans(message)
    for m in _candidate_matches(message):
        candidate = m.group(1)
        digits = re.sub(r"\D", "", candidate)
        if candidate.strip().startswith("+") or digits.startswith("0") or not lo <= len(digits) <= hi:
            continue
        # An amount ("12.500.000 kr") or a reference ("ref 912345678") is no phone.
        if follows_reference(message, m.start(1)) or any(s < m.end(1) and m.start(1) < e for s, e in money):
            continue
        return candidate.strip()
    return None


def national_phone(message: str, country: Optional[str]) -> Optional[str]:
    """national_candidate, placed: E.164, or None when there is none or the country reads it two ways."""
    raw = national_candidate(message, country)
    if not raw:
        return None
    from app.services.crm import format_phone_international

    return format_phone_international(raw, country) or None


def detect_callback_request(message: str, country: Optional[str] = None) -> Optional[dict]:
    """{'number': str|None, 'cue': 'words'|'number'} or None; with 'raw' when the number asked to be
    called on can't be placed (number_to_check): the turn keeps it for the agent instead of asking again.

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
        out = {"number": phone, "cue": "words"}
        raw = None if phone else number_to_check(text)
        if raw:
            out["raw"] = raw
        return out
    if len(text.split()) <= 6:
        # "+1 210 555 0100" / "my number is 210-555-0100" — the number IS
        # the message. Inside a longer sentence it is just contact data
        # the normal extractor already captures. "912345678" from Portugal too: the country's own length.
        first = _candidate_matches(text)
        if phone and _is_phone_shaped(text) and not (first and follows_reference(text, first[0].start(1))):
            return {"number": phone, "cue": "number"}
        national = national_phone(text, country)
        if national:
            return {"number": national, "cue": "number"}
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
