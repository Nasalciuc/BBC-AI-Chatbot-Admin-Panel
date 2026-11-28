"""The fixed words after the card: where the agent should write, and what happens next (18 Nov 2026).

From the "12 probleme Kayak și chat" debate, with the owner's word on the channel: "tu doar spune ca ii
vom scrie, unde: pe email și WhatsApp" — no business number to message; the client says where the agent
should write and leaves that one contact. 20 Nov 2026, the owner: SMS in place of WhatsApp everywhere
("peste tot în loc de WhatsApp să fie SMS"): the agents text from the company's number, and a US visitor
— most of them — has texts, while about a third has WhatsApp. English says "text", Spanish and French
"SMS", as people there say it. A WhatsApp tap from a widget still cached from before (a day at most) is
still answered as WhatsApp: that client typed a number into a field that said WhatsApp.

- A2 (ADR-5a, ADR-10a): the yes on a card that knows the whole trip is answered at once, without the
  model: the trip said back, what happens next, and the buttons SMS, Email and — while the team is
  at work (the Team line) — "I'll wait here" (ADR-5b). In 24 of 25 card chats Alex's first answer asked
  for the email, 6.4 s after the tap, and 7 of the 25 left right there.
- The answer to a channel the client picked: the contact said back with when the agent writes (the Team
  line's promise, ADR-2d) — never a second contact asked for (ADR-5, ADR-8).
- B2 (ADR-6e): "an agent can join you here in about a minute" only while one is online.
- B1 (ADR-6d): one message as a client with no contact leaves, the buttons with it.
- D1 (ADR-4a): the row beside the site's quote form after it refused a send (proactive_i18n: "row").
- C1/C2 (ADR-3a/3b): the card's own channel button, and the pill that stays after "Not now".
- The field side of the owner's contact test (22 Nov 2026, contact_field_test): the yes names one channel and asks
  for its contact, with the field open under it; "Get a fare quote" on a card that knows no whole trip is
  answered at once, contact first, and the trip is asked after it.

English, Spanish and French, like the card (ADR-9b). No amount anywhere (owner, 18 Nov): the price the
client says is kept for the agent, never repeated. No long dash either (owner, 20 Nov: it reads as a machine's;
app.ai.output_guard.no_long_dash takes it out of everything stored, and these words never had to lose it).
"""

from __future__ import annotations

from typing import Optional

CHANNELS: tuple[str, ...] = ("sms", "email")
# What a button does, beside its label: the widget acts on the kind, whatever the language.
KINDS: tuple[str, ...] = ("sms", "email", "wait", "change")

_BUTTONS: dict[str, dict[str, str]] = {
    "sms": {"en": "Text message", "es": "SMS", "fr": "SMS"},
    "email": {"en": "Email", "es": "Correo", "fr": "E-mail"},
    "wait": {"en": "I'll wait here", "es": "Espero aquí", "fr": "J'attends ici"},
    "change": {"en": "Change details", "es": "Cambiar detalles", "fr": "Modifier"},
}
# The card's own channel button (C1) and the pill after "Not now" (C2): SMS on a phone, email on a
# computer (ADR-3a); the row beside the refused form (D1) offers both.
_CARD_CHANNEL: dict[str, dict[str, str]] = {
    "sms": {"en": "Fares by text", "es": "Tarifas por SMS", "fr": "Tarifs par SMS"},
    "email": {"en": "Email me fares", "es": "Tarifas por correo", "fr": "Tarifs par e-mail"},
}

# The card's yes says where the fares go when its answer opens a field (25 Nov, the owner: "100 de procente"). On
# 24-25 Nov 19 of the 24 card chats lost without a contact had tapped "Yes, send me fares" and left at the email
# question that came with it, most within 30 s: the button promised fares and the answer asked for something. Where
# the answer asks the client to choose (no field), the yes stays as it was.
_YES: dict[str, dict[str, str]] = {
    "send": {"en": "Yes, send me fares", "es": "Sí, envíenme tarifas", "fr": "Oui, envoyez-moi les tarifs"},
    "email": {"en": "Yes, email me the fares", "es": "Sí, envíenme las tarifas por correo",
              "fr": "Oui, envoyez-moi les tarifs par e-mail"},
    "sms": {"en": "Yes, text me the fares", "es": "Sí, envíenme las tarifas por SMS",
            "fr": "Oui, envoyez-moi les tarifs par SMS"},
}

_CONFIRM: dict[str, dict[str, str]] = {
    "en": {
        "fares": ("Got it, {line}. Private fares aren't published, so an agent checks them for exactly this trip and "
                  "writes to you. Where should they write: by text or by email?"),
        "beat": ("Got it, {line}. An agent will check if they can beat the price you saw, and write to you. "
                 "Where should they write: by text or by email?"),
    },
    "es": {
        "fares": ("Perfecto: {line}. Las tarifas privadas no se publican, así que un agente las consulta para "
                  "exactamente este viaje y le escribe. ¿Dónde le escribe: SMS o correo?"),
        "beat": ("Perfecto: {line}. Un agente revisará si puede mejorar el precio que vio y le escribirá. "
                 "¿Dónde le escribe: SMS o correo?"),
    },
    "fr": {
        "fares": ("Parfait : {line}. Les tarifs privés ne sont pas publiés, alors un agent les vérifie pour exactement "
                  "ce voyage et vous écrit. Où doit-il vous écrire : SMS ou e-mail ?"),
        "beat": ("Parfait : {line}. Un agent vérifiera s'il peut faire mieux que le prix que vous avez vu, "
                 "et vous écrira. Où doit-il vous écrire : SMS ou e-mail ?"),
    },
}

# The field side of the owner's contact test (contact_field_test, 22 Nov 2026): the yes opens the field at once,
# so the words say where the fares go and ask for that one contact. The other channel stays one tap away (the
# buttons, shown when the client goes back from the field).
_CONFIRM_FIELD: dict[str, dict[tuple[str, str], str]] = {
    "en": {
        ("fares", "sms"): ("Got it, {line}. Private fares aren't published, so an agent checks them for exactly this "
                           "trip and texts them to you. What's your mobile number?"),
        ("fares", "email"): ("Got it, {line}. Private fares aren't published, so an agent checks them for exactly this "
                             "trip and emails them to you. What's your email address?"),
        ("beat", "sms"): ("Got it, {line}. An agent will check if they can beat the price you saw, and text you. "
                          "What's your mobile number?"),
        ("beat", "email"): ("Got it, {line}. An agent will check if they can beat the price you saw, and email you. "
                            "What's your email address?"),
    },
    "es": {
        ("fares", "sms"): ("Perfecto: {line}. Las tarifas privadas no se publican, así que un agente las consulta para "
                           "exactamente este viaje y se las envía por SMS. ¿Cuál es su número de móvil?"),
        ("fares", "email"): ("Perfecto: {line}. Las tarifas privadas no se publican, así que un agente las consulta "
                             "para exactamente este viaje y se las envía por correo. ¿Cuál es su correo?"),
        ("beat", "sms"): ("Perfecto: {line}. Un agente revisará si puede mejorar el precio que vio y le escribirá por "
                          "SMS. ¿Cuál es su número de móvil?"),
        ("beat", "email"): ("Perfecto: {line}. Un agente revisará si puede mejorar el precio que vio y le escribirá "
                            "por correo. ¿Cuál es su correo?"),
    },
    "fr": {
        ("fares", "sms"): ("Parfait : {line}. Les tarifs privés ne sont pas publiés, alors un agent les vérifie pour "
                           "exactement ce voyage et vous les envoie par SMS. Quel est votre numéro de mobile ?"),
        ("fares", "email"): ("Parfait : {line}. Les tarifs privés ne sont pas publiés, alors un agent les vérifie pour "
                             "exactement ce voyage et vous les envoie par e-mail. Quelle est votre adresse e-mail ?"),
        ("beat", "sms"): ("Parfait : {line}. Un agent vérifiera s'il peut faire mieux que le prix que vous avez vu, "
                          "et vous écrira par SMS. Quel est votre numéro de mobile ?"),
        ("beat", "email"): ("Parfait : {line}. Un agent vérifiera s'il peut faire mieux que le prix que vous avez vu, "
                            "et vous écrira par e-mail. Quelle est votre adresse e-mail ?"),
    },
}

# The field side on a card that knows no whole trip (the ads card, the reader's, the tapped note): "Get a fare
# quote" answered at once, contact first. The trip is asked once the contact is in (channel_done, trip_known=False):
# on 19-22 Nov these cards asked the route, the dates and the channel one after another, and 7 of 65 answers left a
# contact.
_CONTACT_FIRST: dict[str, dict[str, str]] = {
    "en": {
        "sms": ("Sure. Private fares aren't published, so an agent checks them for your trip and texts them to you. "
                "What's your mobile number?"),
        "email": ("Sure. Private fares aren't published, so an agent checks them for your trip and emails them to "
                  "you. What's your email address?"),
    },
    "es": {
        "sms": ("Claro. Las tarifas privadas no se publican, así que un agente las consulta para su viaje y se las "
                "envía por SMS. ¿Cuál es su número de móvil?"),
        "email": ("Claro. Las tarifas privadas no se publican, así que un agente las consulta para su viaje y se las "
                  "envía por correo. ¿Cuál es su correo?"),
    },
    "fr": {
        "sms": ("Bien sûr. Les tarifs privés ne sont pas publiés, alors un agent les vérifie pour votre voyage et "
                "vous les envoie par SMS. Quel est votre numéro de mobile ?"),
        "email": ("Bien sûr. Les tarifs privés ne sont pas publiés, alors un agent les vérifie pour votre voyage et "
                  "vous les envoie par e-mail. Quelle est votre adresse e-mail ?"),
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
        "sms": "Done, an agent will text your fares to {contact} {when}.",
        # A tap from a widget cached from before SMS (a day at most): that client chose WhatsApp.
        "whatsapp": "Done, an agent will send your fares on WhatsApp to {contact} {when}.",
        "email": "Done, an agent will email your fares to {contact} {when}.",
        "tail": " Anything they should know, like flexible dates or an airline you prefer? Write it here.",
        "tail_trip": " Where would you like to fly, and roughly when? Write it here.",
    },
    "es": {
        "sms": "Listo: un agente le enviará las tarifas por SMS al {contact} {when}.",
        "whatsapp": "Listo: un agente le enviará las tarifas por WhatsApp al {contact} {when}.",
        "email": "Listo: un agente le enviará las tarifas a {contact} {when}.",
        "tail": " ¿Algo que deba saber, como fechas flexibles o una aerolínea preferida? Escríbalo aquí.",
        "tail_trip": " ¿A dónde quiere volar y, más o menos, cuándo? Escríbalo aquí.",
    },
    "fr": {
        "sms": "C'est noté : un agent vous enverra les tarifs par SMS au {contact} {when}.",
        "whatsapp": "C'est noté : un agent vous enverra les tarifs sur WhatsApp au {contact} {when}.",
        "email": "C'est noté : un agent vous enverra les tarifs à {contact} {when}.",
        "tail": " Quelque chose à savoir, comme des dates flexibles ou une compagnie préférée ? Écrivez-le ici.",
        "tail_trip": " Où souhaitez-vous aller, et à peu près quand ? Écrivez-le ici.",
    },
}
_WAIT: dict[str, tuple[str, str]] = {
    "en": ("Great, an agent can join you here in about a minute.",
           "Great, an agent will write here as soon as one is free. If you need to go, tap Text message or Email "
           "and the fares will reach you there."),
    "es": ("Perfecto: un agente puede unirse aquí en un minuto, más o menos.",
           "Perfecto: un agente le escribirá aquí en cuanto esté libre. Si tiene que irse, toque SMS o "
           "Correo y las tarifas le llegarán allí."),
    "fr": ("Parfait : un agent peut vous rejoindre ici dans une minute environ.",
           "Parfait : un agent vous écrira ici dès qu'il sera libre. Si vous devez partir, touchez SMS ou "
           "E-mail et les tarifs vous y parviendront."),
}
_BEFORE_YOU_GO: dict[str, tuple[str, str]] = {
    "en": ("Before you go, an agent can send you the fares for {line}. Where should they write: by text or by email?",
           "Before you go, where should an agent send you the fares: by text or by email?"),
    "es": ("Antes de irse: un agente puede enviarle las tarifas para {line}. ¿Dónde le escribe: SMS o correo?",
           "Antes de irse: ¿dónde le envía un agente las tarifas: SMS o correo?"),
    "fr": ("Avant de partir : un agent peut vous envoyer les tarifs pour {line}. Où doit-il vous écrire : "
           "SMS ou e-mail ?",
           "Avant de partir : où un agent doit-il vous envoyer les tarifs : SMS ou e-mail ?"),
}
# The same ask in a chat an agent has written in and the client left unanswered (lever 6, 25 Nov): the agent is
# already there, so "your agent", never "an agent".
_BEFORE_YOU_GO_AGENT: dict[str, tuple[str, str]] = {
    "en": ("Before you go, your agent can send you the fares for {line}. Where should they write: by text or by email?",
           "Before you go, where should your agent send you the fares: by text or by email?"),
    "es": ("Antes de irse: su agente puede enviarle las tarifas para {line}. ¿Dónde le escribe: SMS o correo?",
           "Antes de irse: ¿dónde le envía su agente las tarifas: SMS o correo?"),
    "fr": ("Avant de partir : votre agent peut vous envoyer les tarifs pour {line}. Où doit-il vous écrire : "
           "SMS ou e-mail ?",
           "Avant de partir : où votre agent doit-il vous envoyer les tarifs : SMS ou e-mail ?"),
}


def _lang(lang: Optional[str]) -> str:
    return lang if lang in ("es", "fr") else "en"


def button(kind: str, lang: Optional[str] = None) -> dict:
    """{"kind", "label"} for one of KINDS."""
    return {"kind": kind, "label": _BUTTONS[kind][_lang(lang)]}


def yes_label(lang: Optional[str], field: Optional[str]) -> str:
    """The card's yes: where the fares go when its answer opens `field` ("Yes, email me the fares"), else the yes
    as it was ("Yes, send me fares": the answer asks where to write, or the model answers)."""
    return _YES[field if field in CHANNELS else "send"][_lang(lang)]


def card_channel(kind: str, lang: Optional[str] = None) -> dict:
    """The card's own channel button (C1), the pill after "Not now" (C2) and the refused form's row (D1)."""
    return {"kind": kind, "label": _CARD_CHANNEL[kind][_lang(lang)]}


def device_channel(device: Optional[str]) -> str:
    """The card's own channel button and the pill (C1/C2): SMS on a phone, email on a computer (ADR-3a); a computer
    when the widget did not say."""
    return "sms" if device == "phone" else "email"


def field_channel(device: Optional[str]) -> str:
    """The field the yes opens at once (and "Get a fare quote" contact first): email on every device, the base since
    23 Nov. The owner's contact test, 22 Nov 20:05 to 23 Nov 06:30 UTC: the email field opened at once got a contact
    in 9 of 12 chats, the choice of channel in 10 of 18, the SMS field on a phone in 2 of 7 (3 of the other 5 closed
    the chat 10-16 s after it opened); phone visitors free to choose took email over SMS, 16 to 10 on 16-22 Nov.
    SMS stays one tap away under the field, and on a phone the card's own button still offers the fares by text.

    CONTACT_FIELD_PHONE_CHANNEL=sms (app.flags) gives a phone the SMS field again, without a deploy."""
    from app.flags import flag

    if device == "phone" and str(flag("CONTACT_FIELD_PHONE_CHANNEL", "email") or "").strip().lower() == "sms":
        return "sms"
    return "email"


def confirm(line: str, lang: Optional[str] = None, beat: bool = False, field: Optional[str] = None) -> str:
    """A2: the yes answered at once — the trip said back, what happens next, where to write. On the field side of
    the owner's contact test (`field`: the field the widget opens with these words, contact_field_test), the one
    channel is named and its contact asked for."""
    kind = "beat" if beat else "fares"
    if field in CHANNELS:
        return _CONFIRM_FIELD[_lang(lang)][(kind, field)].format(line=line)
    return _CONFIRM[_lang(lang)][kind].format(line=line)


def confirm_buttons(lang: Optional[str], team_online: bool) -> list[dict]:
    """SMS, Email, "I'll wait here" while the team is at work (ADR-5b), and "Change details" (a wrong
    trip is mended in one tap)."""
    kinds = ["sms", "email", *(["wait"] if team_online else []), "change"]
    return [button(k, lang) for k in kinds]


def contact_first(lang: Optional[str], field: str) -> str:
    """The field side's answer to "Get a fare quote" on a card that knows no whole trip: where the fares go, and
    that one contact asked for (contact_field_test). Email for a field that is not one of CHANNELS."""
    return _CONTACT_FIRST[_lang(lang)][field if field in CHANNELS else "email"]


def contact_first_buttons(lang: Optional[str], team_online: bool) -> list[dict]:
    """Under the contact-first answer, shown when the client goes back from the field: both channels, and "I'll
    wait here" while the team is at work. No "Change details": there is no trip to change yet."""
    kinds = ["sms", "email", *(["wait"] if team_online else [])]
    return [button(k, lang) for k in kinds]


def when(lang: Optional[str], team_online: bool) -> str:
    online, offline = _WHEN[_lang(lang)]
    return online if team_online else offline


def channel_done(
    kind: str, contact: str, lang: Optional[str], team_online: bool, *, trip_known: bool = True,
) -> str:
    """The contact the client left on the channel they picked, said back, with when the agent writes; then
    the extras for a known trip, or the trip itself when none is known (#3355)."""
    words = _DONE[_lang(lang)]
    tail = words["tail"] if trip_known else words["tail_trip"]
    return words[kind].format(contact=contact, when=when(lang, team_online)) + tail


def wait_answer(lang: Optional[str], agent_online: bool) -> tuple[str, list[dict]]:
    """B2: the minute is promised only while an agent is online; otherwise the channels stay offered."""
    free, busy = _WAIT[_lang(lang)]
    if agent_online:
        return free, []
    return busy, [button("sms", lang), button("email", lang)]


def before_you_go(line: Optional[str], lang: Optional[str] = None, agent: bool = False) -> tuple[str, list[dict]]:
    """B1: the one message for a client with no contact who is leaving; `agent`: in a chat an agent wrote in."""
    with_line, without = (_BEFORE_YOU_GO_AGENT if agent else _BEFORE_YOU_GO)[_lang(lang)]
    text = with_line.format(line=line) if line else without
    return text, [button("sms", lang), button("email", lang)]


# A goodbye from a client who left no way to reach them, once the before-you-go ask was said (it is said once
# per chat) or when they turned a channel down: nothing is promised ("We'll be in touch soon" was, to a client
# nobody could reach: a quote-card chat on 19 Nov) and nothing is asked again (CodeRabbit #270).
_GOODBYE_NO_CONTACT: dict[str, str] = {
    "en": "Thanks for stopping by. Whenever you'd like the fares, just write here.",
    "es": "Gracias por su visita: cuando quiera las tarifas, escríbanos aquí.",
    "fr": "Merci de votre visite : quand vous voudrez les tarifs, écrivez-nous ici.",
}


def goodbye_without_contact(lang: Optional[str] = None) -> str:
    """The goodbye that promises nothing and asks nothing, for a client with no contact on file."""
    return _GOODBYE_NO_CONTACT[_lang(lang)]


# A channel's contact the server cannot read (25 Nov: an email typed with stars, which the widget's field let through,
# was answered "Email's captured" by the model while nothing was kept): asked again, the field one tap away.
_UNREADABLE: dict[str, dict[str, str]] = {
    "en": {
        "email": "I couldn't read that email address. Could you check it and send it again?",
        "sms": "I couldn't read that number. Could you check it, with the country code, and send it again?",
    },
    "es": {
        "email": "No pude leer ese correo. ¿Puede revisarlo y enviarlo de nuevo?",
        "sms": "No pude leer ese número. ¿Puede revisarlo, con el código de país, y enviarlo de nuevo?",
    },
    "fr": {
        "email": "Je n'ai pas pu lire cette adresse e-mail. Pouvez-vous la vérifier et la renvoyer ?",
        "sms": "Je n'ai pas pu lire ce numéro. Pouvez-vous le vérifier, avec l'indicatif du pays, et le renvoyer ?",
    },
}


def contact_unreadable(kind: str, lang: Optional[str] = None) -> tuple[str, list[dict]]:
    """The answer to a channel whose contact could not be read: that channel asked again (a number for SMS and the
    WhatsApp of a cached widget), its button first, the other one after it."""
    channel = "email" if kind == "email" else "sms"
    other = "sms" if channel == "email" else "email"
    return _UNREADABLE[_lang(lang)][channel], [button(channel, lang), button(other, lang)]


# Test 1 (proactive.form_left, 27 Nov): the buttons of each arm's card, English first (the owner approved these words on
# 27 Nov; Spanish and French after). A and B at night ask where the fares go; B in the team's hours asks for the chat now,
# with the wait button's kind, so its tap joins the line for a person (card_buttons._wait), with the minute promised
# only while an agent is online. The card's "Not now" comes from its replies.
_FORM_LEFT_BUTTONS: dict[str, tuple[tuple[str, str], ...]] = {
    "fl_a": (("sms", "Text me"), ("email", "Email me")),
    "fl_b_night": (("sms", "Text me"), ("email", "Email me")),
    "fl_b_day": (("wait", "Yes, chat now"),),
    "fl_b_day_quiet": (("wait", "Yes, chat"),),
}


def form_left_buttons(variant: Optional[str]) -> list[dict]:
    """The buttons of test 1's card for `variant` (arm A's for any other)."""
    return [{"kind": k, "label": label} for k, label in _FORM_LEFT_BUTTONS.get(variant or "", _FORM_LEFT_BUTTONS["fl_a"])]


def row_buttons(lang: Optional[str] = None) -> list[dict]:
    """D1: both channels beside the refused form."""
    return [card_channel("sms", lang), card_channel("email", lang)]
