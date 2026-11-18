"""The fixed words after the card: where the agent should write, and what happens next (18 Nov 2026).

From the "12 probleme Kayak și chat" debate, with the owner's word on the channel: "tu doar spune ca ii
vom scrie, unde: pe email și WhatsApp" — no business number to message; the client says where the agent
should write and leaves that one contact.

- A2 (ADR-5a, ADR-10a): the yes on a card that knows the whole trip is answered at once, without the
  model: the trip said back, what happens next, and the buttons WhatsApp, Email and — while the team is
  at work (the Team line) — "I'll wait here" (ADR-5b). In 24 of 25 card chats Alex's first answer asked
  for the email, 6.4 s after the tap, and 7 of the 25 left right there.
- The answer to a channel the client picked: the contact said back with when the agent writes (the Team
  line's promise, ADR-2d) — never a second contact asked for (ADR-5, ADR-8).
- B2 (ADR-6e): "an agent can join you here in about a minute" only while one is online.
- B1 (ADR-6d): one message as a client with no contact leaves, the buttons with it.
- D1 (ADR-4a): the row beside the site's quote form after it refused a send (proactive_i18n: "row").
- C1/C2 (ADR-3a/3b): the card's own channel button, and the pill that stays after "Not now".

English, Spanish and French, like the card (ADR-9b). No amount anywhere (owner, 18 Nov): the price the
client says is kept for the agent, never repeated.
"""

from __future__ import annotations

from typing import Optional

CHANNELS: tuple[str, ...] = ("whatsapp", "email")
# What a button does, beside its label: the widget acts on the kind, whatever the language.
KINDS: tuple[str, ...] = ("whatsapp", "email", "wait", "change")

_BUTTONS: dict[str, dict[str, str]] = {
    "whatsapp": {"en": "WhatsApp", "es": "WhatsApp", "fr": "WhatsApp"},
    "email": {"en": "Email", "es": "Correo", "fr": "E-mail"},
    "wait": {"en": "I'll wait here", "es": "Espero aquí", "fr": "J'attends ici"},
    "change": {"en": "Change details", "es": "Cambiar detalles", "fr": "Modifier"},
}
# The card's own channel button (C1) and the pill after "Not now" (C2): WhatsApp on a phone, email on a
# computer (ADR-3a); the row beside the refused form (D1) offers both.
_CARD_CHANNEL: dict[str, dict[str, str]] = {
    "whatsapp": {"en": "Fares on WhatsApp", "es": "Tarifas por WhatsApp", "fr": "Tarifs sur WhatsApp"},
    "email": {"en": "Email me fares", "es": "Tarifas por correo", "fr": "Tarifs par e-mail"},
}

_CONFIRM: dict[str, dict[str, str]] = {
    "en": {
        "fares": ("Got it — {line}. An agent will check live fares for exactly this trip and write to you. "
                  "Where should they write: WhatsApp or email?"),
        "beat": ("Got it — {line}. An agent will check if they can beat the price you saw, and write to you. "
                 "Where should they write: WhatsApp or email?"),
    },
    "es": {
        "fares": ("Perfecto: {line}. Un agente consultará tarifas reales para exactamente este viaje y le escribirá. "
                  "¿Dónde le escribe: WhatsApp o correo?"),
        "beat": ("Perfecto: {line}. Un agente revisará si puede mejorar el precio que vio y le escribirá. "
                 "¿Dónde le escribe: WhatsApp o correo?"),
    },
    "fr": {
        "fares": ("Parfait : {line}. Un agent vérifiera les tarifs réels pour exactement ce voyage et vous écrira. "
                  "Où doit-il vous écrire : WhatsApp ou e-mail ?"),
        "beat": ("Parfait : {line}. Un agent vérifiera s'il peut faire mieux que le prix que vous avez vu, "
                 "et vous écrira. Où doit-il vous écrire : WhatsApp ou e-mail ?"),
    },
}

# When the agent writes: the Team line's promise (app.ai.prompts.team_line), the only time the chat names.
_WHEN: dict[str, tuple[str, str]] = {
    "en": ("within about 30 minutes", "first thing when our team is back"),
    "es": ("en unos 30 minutos", "a primera hora, cuando vuelva el equipo"),
    "fr": ("d'ici 30 minutes environ", "dès le retour de l'équipe"),
}
_DONE: dict[str, dict[str, str]] = {
    "en": {
        "whatsapp": "Done — an agent will send your fares on WhatsApp to {contact} {when}.",
        "email": "Done — an agent will email your fares to {contact} {when}.",
        "tail": " Anything they should know, like flexible dates or an airline you prefer? Write it here.",
    },
    "es": {
        "whatsapp": "Listo: un agente le enviará las tarifas por WhatsApp al {contact} {when}.",
        "email": "Listo: un agente le enviará las tarifas a {contact} {when}.",
        "tail": " ¿Algo que deba saber, como fechas flexibles o una aerolínea preferida? Escríbalo aquí.",
    },
    "fr": {
        "whatsapp": "C'est noté : un agent vous enverra les tarifs sur WhatsApp au {contact} {when}.",
        "email": "C'est noté : un agent vous enverra les tarifs à {contact} {when}.",
        "tail": " Quelque chose à savoir, comme des dates flexibles ou une compagnie préférée ? Écrivez-le ici.",
    },
}
_WAIT: dict[str, tuple[str, str]] = {
    "en": ("Great — an agent can join you here in about a minute.",
           "Great — an agent will write here as soon as one is free. If you need to go, tap WhatsApp or Email "
           "and the fares will reach you there."),
    "es": ("Perfecto: un agente puede unirse aquí en un minuto, más o menos.",
           "Perfecto: un agente le escribirá aquí en cuanto esté libre. Si tiene que irse, toque WhatsApp o "
           "Correo y las tarifas le llegarán allí."),
    "fr": ("Parfait : un agent peut vous rejoindre ici dans une minute environ.",
           "Parfait : un agent vous écrira ici dès qu'il sera libre. Si vous devez partir, touchez WhatsApp ou "
           "E-mail et les tarifs vous y parviendront."),
}
_BEFORE_YOU_GO: dict[str, tuple[str, str]] = {
    "en": ("Before you go — an agent can send you the fares for {line}. Where should they write: WhatsApp or email?",
           "Before you go — where should an agent send you the fares: WhatsApp or email?"),
    "es": ("Antes de irse: un agente puede enviarle las tarifas para {line}. ¿Dónde le escribe: WhatsApp o correo?",
           "Antes de irse: ¿dónde le envía un agente las tarifas: WhatsApp o correo?"),
    "fr": ("Avant de partir : un agent peut vous envoyer les tarifs pour {line}. Où doit-il vous écrire : "
           "WhatsApp ou e-mail ?",
           "Avant de partir : où un agent doit-il vous envoyer les tarifs : WhatsApp ou e-mail ?"),
}


def _lang(lang: Optional[str]) -> str:
    return lang if lang in ("es", "fr") else "en"


def button(kind: str, lang: Optional[str] = None) -> dict:
    """{"kind", "label"} for one of KINDS."""
    return {"kind": kind, "label": _BUTTONS[kind][_lang(lang)]}


def card_channel(kind: str, lang: Optional[str] = None) -> dict:
    """The card's own channel button (C1), the pill after "Not now" (C2) and the refused form's row (D1)."""
    return {"kind": kind, "label": _CARD_CHANNEL[kind][_lang(lang)]}


def device_channel(device: Optional[str]) -> str:
    """WhatsApp on a phone, email on a computer (ADR-3a); a computer when the widget did not say."""
    return "whatsapp" if device == "phone" else "email"


def confirm(line: str, lang: Optional[str] = None, beat: bool = False) -> str:
    """A2: the yes answered at once — the trip said back, what happens next, where to write."""
    return _CONFIRM[_lang(lang)]["beat" if beat else "fares"].format(line=line)


def confirm_buttons(lang: Optional[str], team_online: bool) -> list[dict]:
    """WhatsApp, Email, "I'll wait here" while the team is at work (ADR-5b), and "Change details" (a wrong
    trip is mended in one tap)."""
    kinds = ["whatsapp", "email", *(["wait"] if team_online else []), "change"]
    return [button(k, lang) for k in kinds]


def when(lang: Optional[str], team_online: bool) -> str:
    online, offline = _WHEN[_lang(lang)]
    return online if team_online else offline


def channel_done(kind: str, contact: str, lang: Optional[str], team_online: bool) -> str:
    """The contact the client left on the channel they picked, said back, with when the agent writes."""
    words = _DONE[_lang(lang)]
    return words[kind].format(contact=contact, when=when(lang, team_online)) + words["tail"]


def wait_answer(lang: Optional[str], agent_online: bool) -> tuple[str, list[dict]]:
    """B2: the minute is promised only while an agent is online; otherwise the channels stay offered."""
    free, busy = _WAIT[_lang(lang)]
    if agent_online:
        return free, []
    return busy, [button("whatsapp", lang), button("email", lang)]


def before_you_go(line: Optional[str], lang: Optional[str] = None) -> tuple[str, list[dict]]:
    """B1: the one message for a client with no contact who is leaving."""
    with_line, without = _BEFORE_YOU_GO[_lang(lang)]
    text = with_line.format(line=line) if line else without
    return text, [button("whatsapp", lang), button("email", lang)]


# A goodbye from a client who left no way to reach them, after the before-you-go ask was already said:
# nothing is promised ("We'll be in touch soon" was, to a client nobody could reach: 19 Nov, Sydney →
# Amman). With a channel they turned down, no ask at all.
_GOODBYE_NO_CONTACT: dict[str, tuple[str, str]] = {
    "en": ("Thanks for stopping by. Whenever you'd like the fares, leave your WhatsApp or email here and an "
           "agent will send them.",
           "Thanks for stopping by — whenever you'd like the fares, just write here."),
    "es": ("Gracias por su visita. Cuando quiera las tarifas, deje aquí su WhatsApp o su correo y un agente se "
           "las enviará.",
           "Gracias por su visita: cuando quiera las tarifas, escríbanos aquí."),
    "fr": ("Merci de votre visite. Quand vous voudrez les tarifs, laissez ici votre WhatsApp ou votre e-mail et "
           "un agent vous les enverra.",
           "Merci de votre visite : quand vous voudrez les tarifs, écrivez-nous ici."),
}


def goodbye_without_contact(lang: Optional[str] = None, *, ask: bool = True) -> tuple[str, list[dict]]:
    """The goodbye that promises nothing, for a client with no contact on file: the channels offered once
    more (with their buttons), or — when the client turned a channel down — none."""
    with_ask, without = _GOODBYE_NO_CONTACT[_lang(lang)]
    if not ask:
        return without, []
    return with_ask, [button("whatsapp", lang), button("email", lang)]


def row_buttons(lang: Optional[str] = None) -> list[dict]:
    """D1: both channels beside the refused form."""
    return [card_channel("whatsapp", lang), card_channel("email", lang)]
