"""The contact a client gives in the conversation itself.

A chat with no form behind it (the proactive card, the note, the corner
button) collects the name, the email and the phone in the conversation, and
the conversation row is where they land — the request from the widget never
carries them. Every check in the pipeline (the missing fields, the summary,
the CRM gate and its payload) reads the request's `visitor`, so on 16-17 Nov
none of the 13 chats could reach the CRM, even with a name, an email and a
phone in the chat: `with_stored_contact` fills the request's gaps from the row.

Two answers were lost on the way:
- Alex asks "And your name?" and the client answers "Paul Moreau". The
  extractor never takes a name from free text (a fragment like "looking for"
  must never become one), so all six names given that night were lost and the
  callback reply asked for them again.
- Alex asks "would a text or a quick call work better?" and the client answers
  "Text". No channel phrase matched, so the wish was lost and the reply promised
  a call to a client who had said she can't take one.

An answer is read only when Alex (or an operator) has just asked the question,
and only when it has the shape of an answer.
"""

from __future__ import annotations

import re
from typing import Optional

from app.models.chat import VisitorInfo


def with_stored_contact(visitor: Optional[VisitorInfo], stored: Optional[dict]) -> VisitorInfo:
    """The request's contact with its gaps filled from the conversation row.
    What the widget sent wins; the row only fills what it left empty — and
    completes a first name: a form that sent "John" on every turn would
    otherwise write "John" back over the "John Smith" the client gave when
    Alex asked for the last name."""
    visitor = visitor or VisitorInfo()
    row = stored or {}
    fill = {}
    for field, column in (("name", "visitor_name"), ("email", "visitor_email"), ("phone", "visitor_phone")):
        value = row.get(column)
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        sent = (getattr(visitor, field, None) or "").strip()
        if not sent:
            fill[field] = value
        elif field == "name" and _completes(sent, value):
            fill[field] = value
    return visitor.model_copy(update=fill) if fill else visitor


def _completes(first: str, full: str) -> bool:
    """"John Smith" completes "John"; "Johnny" or "Mary Smith" does not."""
    sent, stored = first.split(), full.split()
    return len(sent) == 1 and len(stored) > 1 and stored[0].lower() == sent[0].lower()


def last_question(history: Optional[list]) -> str:
    """The newest message from Alex or an operator: what the client's message
    in this turn answers (this turn's reply does not exist yet). '' when
    there is none."""
    for msg in reversed(history or []):
        if isinstance(msg, dict) and msg.get("role") in ("ai", "agent"):
            return (msg.get("content") or "").strip()
    return ""


# "And your name?", "What's your last name so…?", "What name should they
# address you by?", "your name, so the consultant knows who to address?",
# "the name on the booking?". Not "the name of the airline you prefer?".
_NAME_QUESTION_RE = re.compile(
    r"\b(?:(?:your|full|first|last)\s+name"
    r"|what\s+name|name\s+should|who\s+to\s+address"
    r"|name\s+(?:for|on)\s+the\s+(?:booking|reservation|ticket)"
    r"|who\s+(?:am\s+i|i'?m)\s+(?:speaking|chatting)\s+(?:with|to)"
    # Spanish, French, Portuguese (#3325, 18 Nov 2026: "¿Cuál es su nombre?" was not read as the question)
    r"|(?:su|tu)\s+nombre|nombre\s+completo|c[oó]mo\s+(?:se|te)\s+llama"
    r"|(?:votre|ton)\s+nom|nom\s+complet|comment\s+(?:vous\s+vous\s+appelez|tu\s+t'?appelles?)"
    r"|(?:seu|teu)\s+nome|nome\s+completo|como\s+(?:voc[eê]\s+)?se\s+chama)\b",
    re.IGNORECASE,
)
_LAST_NAME_QUESTION_RE = re.compile(r"\b(?:last|family|sur)\s*name\b", re.IGNORECASE)
# "would a text or a quick call work better?", "call or text?", "text or call?"
_CHANNEL_QUESTION_RE = re.compile(
    r"\b(?:text|sms|mensaje|texto|message|mensagem)\b[^?]{0,40}\b(?:call|phone|llamada|appel|liga[cç][aã]o)\b"
    r"|\b(?:call|phone|llamada|appel|liga[cç][aã]o)\b[^?]{0,40}\b(?:text|sms|mensaje|texto|message|mensagem)\b",
    re.IGNORECASE,
)


_QUESTION_SENTENCE_RE = re.compile(r"[^.!?\n]*\?")


def _asked(question: str, pattern: re.Pattern) -> bool:
    """True when one of the message's QUESTIONS matches — not a statement
    next to it: "Thanks, your name is noted. Which dates work?" asks for
    dates, not for a name."""
    return any(pattern.search(s) for s in _QUESTION_SENTENCE_RE.findall(question or ""))


def asks_for_name(question: str) -> bool:
    return _asked(question, _NAME_QUESTION_RE)


def asks_for_last_name(question: str) -> bool:
    return _asked(question, _LAST_NAME_QUESTION_RE)


def asks_for_channel(question: str) -> bool:
    return _asked(question, _CHANNEL_QUESTION_RE)


# Words that answer something, never a name: "yes", "Text", "Price",
# "Waiting for price", "Email is best", "Sorry .ca", "Next week",
# "Round trip", "Just me".
_NOT_A_NAME = frozenset("""
yes yeah yep no nope ok okay sure fine thanks thank you hi hello hey
text txt sms call phone email mail number price prices fare fares quote
what why how when where who which later now not please sorry waiting wait
business economy first premium class ticket tickets flight flights trip
round one way return nonstop direct seat seats
today tomorrow tonight next week weekend month year morning afternoon evening night
asap soon anytime monday tuesday wednesday thursday friday saturday sunday
january february march july september october november december
just only us we our family wife husband kids adults
flexible any anything whatever none nothing idk unsure
the a an and or my is it its it's i i'm im me
español espanol spanish inglés ingles english francés frances français francais french portugués portugues
português portuguese por favor porfavor gracias merci plaît plait svp obrigado obrigada hola olá bonjour oui
sí non não nao habla hablo parle parlez falo fala mensaje mensajes texto mensagem llamada correo courriel
""".split())
_NAME_INTRO_RE = re.compile(r"^(?:it'?s|its|this\s+is|i'?m|im|i\s+am|my\s+name\s+is|name\s+is|name:)\s+", re.I)
_NAME_WORD_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|['’.-])*$")


def _straight(text: str) -> str:
    """A phone types ’ (U+2019) by default; the patterns here read '."""
    return (text or "").replace("’", "'").replace("‘", "'")


# A yes on its own, in the languages the summary asks in: "Sim" alone answers, "Sim Lee" is a name
# (CodeRabbit #270: a bare Portuguese "sim" was stored as the client's name).
_A_BARE_YES = frozenset({"sim", "si", "claro", "vale", "certo", "perfeito", "perfecto", "exacto", "isso", "bueno",
                         "parfait", "daccord", "d'accord"})
# A Spanish yes that is also an English first name: a bare yes only while the client writes Spanish
# ("Dale" is a name in an English chat; CodeRabbit #270).
_A_SPANISH_YES_ALSO_A_NAME = frozenset({"dale"})


def name_from_answer(message: str) -> Optional[str]:
    """The name in a reply to "your name?" — one to four words of letters
    ("Paul Moreau", "maribel", "Mary-Ann O'Neil"), None for anything else
    (a channel, a price question, an email, a number, a sentence)."""
    text = _straight(message).strip().strip(".!,;")
    text = _NAME_INTRO_RE.sub("", text).strip().strip(".!,;")
    if not text or len(text) > 60 or "@" in text or any(ch.isdigit() for ch in text):
        return None
    words = text.split()
    if not 1 <= len(words) <= 4:
        return None
    if len(words) == 1 and words[0].lower() in _A_BARE_YES:
        return None
    if len(words) == 1 and words[0].lower() in _A_SPANISH_YES_ALSO_A_NAME:
        from app.pipeline.language import current_lang

        if current_lang.get() == "es":
            return None
    if any(not _NAME_WORD_RE.match(w) for w in words):
        return None
    if any(w.lower().strip(".'’") in _NOT_A_NAME for w in words):
        return None
    if sum(ch.isalpha() for ch in text) < 2:
        return None
    # "maribel" → "Maribel"; a name the client capitalised stays as written.
    return " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in words)


def full_name(known: Optional[str], answer: str, last_name_asked: bool) -> str:
    """The answer to "your last name?" joins the first name already known."""
    known = (known or "").strip()
    if last_name_asked and known and len(known.split()) == 1 and len(answer.split()) == 1:
        return f"{known} {answer}"
    return answer


def name_to_store(known: Optional[str], answer: Optional[str], last_name_asked: bool) -> Optional[str]:
    """The name this answer gives us, or None when it gives nothing new:
    the first name we lacked; the last name joined to a known first name;
    the full name completing a known first name ("Lena" → "Lena Smith").
    A name already known is never replaced by a different one."""
    if not answer:
        return None
    known = (known or "").strip()
    if not known:
        return answer
    if last_name_asked and len(known.split()) == 1 and len(answer.split()) == 1:
        return full_name(known, answer, last_name_asked)
    if _completes(known, answer):
        return answer
    return None


# Spanish, French and Portuguese too (#3325: "mensaje de texto" read as nothing, so a call task).
_TEXT_ANSWER_RE = re.compile(
    r"\b(?:text(?:ing|s)?|txt|sms|message|whats\s*app|mensajes?|textos?|texte|mensage(?:m|ns)"
    r"|escr[ií]b(?:eme|ame|anme)|[ée]crivez[- ]moi|escreva)\b", re.I)
_CALL_ANSWER_RE = re.compile(
    r"\b(?:call|calls|phone\s+call|ring|llamada|ll[aá]m(?:ame|enme|eme)|llamar|appel(?:ez)?|appeler"
    r"|liga[cç][aã]o|ligar|me\s+liga)\b", re.I)
_EMAIL_ANSWER_RE = re.compile(r"\b(?:e-?mail|correo(?:\s+electr[oó]nico)?|courriel)\b", re.I)
# "I can't always take a call", "no calls", "don't text me, call": a channel
# right after a negation in the same clause is ruled out, not chosen.
_NEGATION_RE = re.compile(
    r"\b(?:can't|cant|cannot|can\s+not|don't|dont|do\s+not|won't|wont|no|never|not"
    r"|pas|ne|n'|não|nao|nunca|jamais|jamás|jamas)\b", re.I)
_CLAUSE_END_RE = re.compile(r"[.?!,;]")


def _chosen(pattern: re.Pattern, text: str) -> bool:
    for m in pattern.finditer(text):
        before = text[max(0, m.start() - 25):m.start()]
        before = _CLAUSE_END_RE.split(before)[-1]
        if not _NEGATION_RE.search(before):
            return True
    return False


def channel_from_answer(message: str) -> Optional[str]:
    """'text', 'email' or 'call' for a reply to "a text or a quick call?":
    "Text", "Text please. I can't always take a call", "Email is best",
    "a call is fine", "don't text me, call". None when the reply chooses no
    channel or more than one."""
    text = _straight(message)
    found = [
        channel
        for channel, pattern in (("text", _TEXT_ANSWER_RE), ("email", _EMAIL_ANSWER_RE), ("call", _CALL_ANSWER_RE))
        if _chosen(pattern, text)
    ]
    return found[0] if len(found) == 1 else None


def _wishes(stored_channel: Optional[str]) -> list[str]:
    return [p.strip() for p in (stored_channel or "").split(",") if p.strip()]


def calls_refused(stored_channel: Optional[str]) -> bool:
    return "no calls" in _wishes(stored_channel)


def callback_channel(message: str, cue: str, stored_channel: Optional[str]) -> str:
    """How the client wants to be reached on the number just given:
    'call' when they asked to be called ("call me", the JOSEF case), 'whatsapp'
    when they chose WhatsApp (in this message, or earlier: the button after the
    card), 'text' when they chose texts (in this message, or earlier: "Text",
    "text only", "no calls" — never over an earlier "no texts"), 'any' when they
    gave a number and said nothing that decides it.

    WhatsApp stays WhatsApp: a client who chose it and then sent a bare number
    was noted "ASKED FOR TEXTS" and told "will text you" (CodeRabbit #270)."""
    from app.pipeline.entity_extractor import contact_channel_of

    now = channel_from_answer(message)
    whatsapp_now = "whatsapp" in _wishes(contact_channel_of(message))
    if now == "call":
        return now
    if whatsapp_now:
        return "whatsapp"
    if now == "text":
        return now
    if cue == "words":
        return "call"
    wishes = _wishes(stored_channel)
    if "whatsapp" in wishes:
        return "whatsapp"
    if "no texts" not in wishes and ("text" in wishes or "no calls" in wishes):
        return "text"
    return "any"


def channel_after_call_request(stored_channel: Optional[str]) -> Optional[str]:
    """The wishes left once the client asks, now, to be called: "no calls"
    and a wish for texts are withdrawn by it; the rest ("email", "no
    texts", …) stays. None when nothing is left."""
    kept = [w for w in _wishes(stored_channel) if w not in ("no calls", "text")]
    return ", ".join(kept) or None


def with_turn_contact(visitor: VisitorInfo, sent: Optional[VisitorInfo], turn: dict) -> VisitorInfo:
    """`visitor` once this turn's contact is in. A field the widget did not
    send takes the value this turn gave, so a corrected email replaces the
    one the conversation row held; a field the widget sent keeps it."""
    fill = {}
    for field in ("name", "email", "phone"):
        value = turn.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        if (getattr(sent, field, None) or "").strip():
            continue
        if value.strip() != (getattr(visitor, field, None) or ""):
            fill[field] = value.strip()
    return visitor.model_copy(update=fill) if fill else visitor
