"""The client's language, when a message says plainly it is Spanish, French or Portuguese.

Alex (the model) already answers in the client's language. What broke on 18 Nov 2026 (chat #3325,
Spanish) were the fixed pieces around it: English templates took over at the phone number, the name
question asked in Spanish was not recognised, "Español Por Favor" was stored as the name, "mensaje de
texto" was not read as a wish for texts, and an English summary was re-asked until the client left.

detect_lang() reads one message by the words no English sentence uses ("hola", "gracias", "merci",
"obrigado", "por favor", …); the turn keeps what a message said (conversation metadata `client_lang`,
sticky: a later "sí" or a phone number says nothing). current_lang carries it for the turn, so the
templates (app/ai/templates.py) speak it. About 4% of Kayak visitors browse in another language,
and only 4 of 652 chats since 1 Oct were written in one: rare, but each one lost was a whole lead.
"""

from __future__ import annotations

import contextvars
import re
import unicodedata
from typing import Optional

LANGS = ("es", "fr", "pt")

# Words of one language that an English message does not use. Kept distinctive: words two of these
# languages share count for both ("ida", "porque", "texto"); words English shares ("no", "me", "message")
# and words in place or month names ("Buenos Aires", "Mon 10 Dec") are left out — greetings that hold
# them are phrases below.
_MARKERS: dict[str, frozenset[str]] = {
    "es": frozenset("""
        hola gracias quiero quisiera necesito vuelo vuelos boleto boletos pasaje pasajes cuanto cuanta
        cuantos precio precios ida vuelta mensaje mensajes texto llamada llamar llamame correo nombre
        telefono espanol ingles hablo habla favor senor senora viaje
        viajar fecha fechas personas adultos ninos salida regreso tambien donde cuando porque usted
        ustedes estoy tengo puedo podria quiero mandame enviame escribame escribeme llamenme somos
        """.split()),
    "fr": frozenset("""
        bonjour bonsoir merci voudrais veux billet billets vol vols prix aller francais anglais parle
        parlez je nous vous oui pas svp plait avec pour appelez appeler courriel
        voyage voyager personnes adultes enfants depart retour aussi quand pourquoi suis peux
        pourriez envoyez ecrivez
        """.split()),
    "pt": frozenset("""
        ola obrigado obrigada quero preciso voo voos passagem passagens quanto preco ida volta
        mensagem mensagens ligacao ligar telefone nome portugues ingles falo fala texto favor
        senhor senhora viagem datas pessoas adultos criancas voce voces nao sim tambem onde quando
        porque tenho posso meu minha envie mande escreva
        """.split()),
}
# Phrases that settle it alone.
_PHRASES: dict[str, tuple[str, ...]] = {
    "es": ("en espanol", "no hablo ingles", "espanol por favor", "de ida y vuelta", "mensaje de texto",
           "buenos dias", "buenas tardes", "buenas noches"),
    "fr": ("s il vous plait", "en francais", "je ne parle pas", "je voudrais", "est ce que", "bonne journee"),
    "pt": ("em portugues", "nao falo ingles", "mensagem de texto", "muito obrigado", "muito obrigada",
           "bom dia", "boa tarde", "boa noite"),
}
_EN = frozenset("""
    the and to i you my me is are am for please want need flight flights from with what when how hi
    hello thanks thank yes call text email can we it this that have will would like business class
    ticket tickets trip travel price fare fares round way one dates date just only
    """.split())

current_lang: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("client_lang", default=None)


def _fold(text: str) -> str:
    """lower, accents off, apostrophes and punctuation as spaces ("S'il vous plaît" → "s il vous plait")."""
    decomposed = unicodedata.normalize("NFKD", (text or "").lower())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z]+", " ", plain).strip()


def detect_lang(text: Optional[str]) -> Optional[str]:
    """'es', 'fr' or 'pt' when the message says plainly it is in that language; None otherwise (English,
    a number, an email, a one-word "ok")."""
    folded = _fold(text or "")
    if not folded:
        return None
    padded = f" {folded} "
    for lang in LANGS:
        if any(f" {p} " in padded for p in _PHRASES[lang]):
            return lang
    words = folded.split()
    scores = {lang: sum(1 for w in words if w in _MARKERS[lang]) for lang in LANGS}
    best = max(LANGS, key=lambda lang: scores[lang])
    n = scores[best]
    if n == 0:
        return None
    if sum(1 for lang in LANGS if scores[lang] == n) > 1:
        return None  # "ida" alone could be Spanish or Portuguese
    english = sum(1 for w in words if w in _EN)
    if n >= 2 and n > english:
        return best
    if english == 0 and len(words) <= 6:
        return best
    return None


def turn_lang(message: Optional[str], stored: Optional[str]) -> Optional[str]:
    """The language this turn speaks: what this message says, else what an earlier one said."""
    found = detect_lang(message)
    if found:
        return found
    return stored if stored in LANGS else None
