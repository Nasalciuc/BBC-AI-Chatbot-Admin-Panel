"""The card's words in Spanish and French, beside the English ones (ADR-9b, 18 Nov 2026).

About 4% of the Kayak visitors browse in another language, Spanish and French 3% of them together
(13 Nov on: 62 and 54 of 3,772). The card spoke English to all of them, and #3325 answered it in
Spanish. The widget says which language the browser uses (`lang`: "es", "fr"); every sentence the card
and its chat start with comes from here, so /offer and /init still write the same words. English is
the default and stays word for word what it was. Nothing here names an amount (owner, 18 Nov).
"""

from __future__ import annotations

from typing import Optional

# The languages the card speaks besides English.
CARD_LANGS: tuple[str, ...] = ("es", "fr")


def card_lang(raw: object) -> Optional[str]:
    """"es" or "fr" from what the widget sends ("es", "es-MX", "fr-CA"); None (English) for anything else."""
    if not isinstance(raw, str):
        return None
    code = raw.strip().lower()[:2]
    return code if code in CARD_LANGS else None


# Every sentence of proactive.compose, by language. {hello}, {line} and {dest} are filled there.
SENTENCES: dict[str, dict[str, str]] = {
    "en": {
        "hello": "Hi, I'm {persona} from {brand}.",
        "quote_contact_line": ("{hello} Rather not fill in the whole form? I can pass {line} to an agent right here, "
                               "and they'll email you the fares."),
        "quote_contact": ("{hello} Rather not fill in the whole form? Tell me here where and roughly when, "
                          "and an agent will email you the fares."),
        "quote_line": "{hello} Looks like the quote form is giving you trouble. Can I help? An agent can check fares for {line}.",
        "quote": ("{hello} Looks like the quote form is giving you trouble. Can I help? "
                  "Tell me where and roughly when, and an agent will check fares for you."),
        "exit_line": "{hello} Before you go: want an agent to check fares for {line}?",
        "exit": "{hello} Before you go: want an agent to check business class fares for you?",
        "full": "{hello} I see you're looking at {line}. Want an agent to check private fares for exactly this trip?",
        # C5 (ADR-2b): the client says the price, an agent checks if they can beat it — never Alex.
        "full_beat": ("{hello} I see you're looking at {line}. Already seen a price? "
                      "An agent checks if they can beat it, with real fares by text or email."),
        "dest_date": "{hello} I see you're looking at {line}. Want an agent to check {cabin_fares} fares for these dates?",
        "dest": "{hello} Planning a trip to {dest}? Tell me roughly when, and an agent will check {cabin_fares} fares for you.",
        # The ads card asks one yes, as the Kayak card does (owner, 23 Nov): the dates and the origin come after
        # the contact (card_words.contact_first, channel_done).
        "ads_dest": "{hello} Want an agent to send you today's {cabin_fares} fares to {dest}?",
        "places": ("{hello} Where are you thinking of flying? "
                   "Tap one or type a city, and an agent will check business class fares for you."),
        "open": ("{hello} Planning a business class trip? "
                 "Tell me where and roughly when, and an agent will check fares for you."),
        # The Meta ad's card on a page that names no place (25 Nov): one yes, as the ads card asks it.
        "social_open": "{hello} Planning a business class trip? Want an agent to send you today's best fares?",
        # D1 (ADR-4a): the row beside a refused quote form. A shortcut, never "you made a mistake".
        # The phone card's own words (ADR A3, 20 Nov): under the trip as its title, only the question of the
        # sentence above it — the chat still opens with the whole sentence. Each one ends that sentence.
        "ask_full": "Want an agent to check private fares for exactly this trip?",
        "ask_full_beat": "Already seen a price? An agent checks if they can beat it, with real fares by text or email.",
        "ask_dest_date": "Want an agent to check {cabin_fares} fares for these dates?",
        "ask_dest": "Tell me roughly when, and an agent will check {cabin_fares} fares for you.",
        "ask_ads_dest": "Want an agent to send you today's {cabin_fares} fares to {dest}?",
        "row_line": "Faster than the form: an agent can send you the fares for {line} by text or by email.",
        "row": "Faster than the form: an agent can send you the fares by text or by email.",
    },
    "es": {
        "hello": "Hola, soy {persona}, de {brand}.",
        "quote_contact_line": ("{hello} ¿Prefiere no llenar todo el formulario? Puedo pasarle {line} a un agente "
                               "aquí mismo, y le enviará las tarifas por correo."),
        "quote_contact": ("{hello} ¿Prefiere no llenar todo el formulario? Dígame aquí adónde y más o menos "
                          "cuándo, y un agente le enviará las tarifas por correo."),
        "quote_line": ("{hello} Parece que el formulario le está dando problemas. ¿Le ayudo? "
                       "Un agente puede consultar tarifas para {line}."),
        "quote": ("{hello} Parece que el formulario le está dando problemas. ¿Le ayudo? "
                  "Dígame adónde y más o menos cuándo, y un agente consultará las tarifas."),
        "exit_line": "{hello} Antes de irse: ¿quiere que un agente consulte tarifas para {line}?",
        "exit": "{hello} Antes de irse: ¿quiere que un agente consulte tarifas en clase Business para usted?",
        "full": "{hello} Veo que busca {line}. ¿Quiere que un agente consulte tarifas privadas para exactamente este viaje?",
        "full_beat": ("{hello} Veo que busca {line}. ¿Ya vio un precio? "
                      "Un agente revisa si puede mejorarlo, con tarifas reales por SMS o correo."),
        "dest_date": "{hello} Veo que busca {line}. ¿Quiere que un agente consulte tarifas {cabin_fares} para esas fechas?",
        "dest": ("{hello} ¿Planea un viaje a {dest}? Dígame más o menos cuándo, "
                 "y un agente consultará tarifas {cabin_fares} para usted."),
        "ads_dest": "{hello} ¿Quiere que un agente le envíe las tarifas de hoy {cabin_fares} a {dest}?",
        "places": ("{hello} ¿Adónde piensa volar? Toque una opción o escriba una ciudad, "
                   "y un agente consultará tarifas en clase Business para usted."),
        "open": ("{hello} ¿Planea un viaje en clase Business? Dígame adónde y más o menos cuándo, "
                 "y un agente consultará las tarifas para usted."),
        "social_open": "{hello} ¿Planea un viaje en clase Business? ¿Quiere que un agente le envíe las mejores tarifas de hoy?",
        "ask_full": "¿Quiere que un agente consulte tarifas privadas para exactamente este viaje?",
        "ask_full_beat": "¿Ya vio un precio? Un agente revisa si puede mejorarlo, con tarifas reales por SMS o correo.",
        "ask_dest_date": "¿Quiere que un agente consulte tarifas {cabin_fares} para esas fechas?",
        "ask_dest": "Dígame más o menos cuándo, y un agente consultará tarifas {cabin_fares} para usted.",
        "ask_ads_dest": "¿Quiere que un agente le envíe las tarifas de hoy {cabin_fares} a {dest}?",
        "row_line": "Más rápido que el formulario: un agente puede enviarle las tarifas para {line} por SMS o por correo.",
        "row": "Más rápido que el formulario: un agente puede enviarle las tarifas por SMS o por correo.",
    },
    "fr": {
        "hello": "Bonjour, je suis {persona}, de {brand}.",
        "quote_contact_line": ("{hello} Vous préférez ne pas remplir tout le formulaire ? Je peux transmettre {line} "
                               "à un agent ici même, et il vous enverra les tarifs par e-mail."),
        "quote_contact": ("{hello} Vous préférez ne pas remplir tout le formulaire ? Dites-moi ici où et à peu près "
                          "quand, et un agent vous enverra les tarifs par e-mail."),
        "quote_line": ("{hello} On dirait que le formulaire vous pose problème. Je peux vous aider ? "
                       "Un agent peut vérifier les tarifs pour {line}."),
        "quote": ("{hello} On dirait que le formulaire vous pose problème. Je peux vous aider ? "
                  "Dites-moi où et à peu près quand, et un agent vérifiera les tarifs."),
        "exit_line": "{hello} Avant de partir : voulez-vous qu'un agent vérifie les tarifs pour {line} ?",
        "exit": "{hello} Avant de partir : voulez-vous qu'un agent vérifie les tarifs en classe Affaires pour vous ?",
        "full": ("{hello} Je vois que vous regardez {line}. "
                 "Voulez-vous qu'un agent vérifie des tarifs privés pour exactement ce voyage ?"),
        "full_beat": ("{hello} Je vois que vous regardez {line}. Vous avez déjà vu un prix ? "
                      "Un agent vérifie s'il peut faire mieux, avec de vrais tarifs par SMS ou e-mail."),
        "dest_date": ("{hello} Je vois que vous regardez {line}. "
                      "Voulez-vous qu'un agent vérifie les tarifs {cabin_fares} pour ces dates ?"),
        "dest": ("{hello} Vous préparez un voyage à {dest} ? Dites-moi à peu près quand, "
                 "et un agent vérifiera les tarifs {cabin_fares} pour vous."),
        "ads_dest": "{hello} Voulez-vous qu'un agent vous envoie les tarifs du jour {cabin_fares} pour {dest} ?",
        "places": ("{hello} Où pensez-vous partir ? Touchez une option ou écrivez une ville, "
                   "et un agent vérifiera les tarifs en classe Affaires pour vous."),
        "open": ("{hello} Vous préparez un voyage en classe Affaires ? Dites-moi où et à peu près quand, "
                 "et un agent vérifiera les tarifs pour vous."),
        "social_open": ("{hello} Vous préparez un voyage en classe Affaires ? "
                        "Voulez-vous qu'un agent vous envoie les meilleurs tarifs du jour ?"),
        "ask_full": "Voulez-vous qu'un agent vérifie des tarifs privés pour exactement ce voyage ?",
        "ask_full_beat": ("Vous avez déjà vu un prix ? Un agent vérifie s'il peut faire mieux, "
                          "avec de vrais tarifs par SMS ou e-mail."),
        "ask_dest_date": "Voulez-vous qu'un agent vérifie les tarifs {cabin_fares} pour ces dates ?",
        "ask_dest": "Dites-moi à peu près quand, et un agent vérifiera les tarifs {cabin_fares} pour vous.",
        "ask_ads_dest": "Voulez-vous qu'un agent vous envoie les tarifs du jour {cabin_fares} pour {dest} ?",
        "row_line": "Plus rapide que le formulaire : un agent peut vous envoyer les tarifs pour {line} par SMS ou par e-mail.",
        "row": "Plus rapide que le formulaire : un agent peut vous envoyer les tarifs par SMS ou par e-mail.",
    },
}

# The buttons under each card (proactive._REPLIES keeps the English ones). "Not now" last, as there:
# the widget closes the card on it and never sends it (bbc-widget triggers.ts, isNotNow).
REPLIES: dict[str, dict[str, list[str]]] = {
    "es": {
        "kayak": ["Sí, envíenme tarifas", "Cambiar detalles", "Ahora no"],
        "quote": ["Sí, envíenme tarifas", "Tengo una pregunta", "Ahora no"],
        "quote_contact": ["Envíenme las tarifas por correo", "Tengo una pregunta", "Ahora no"],
        "reader": ["Quiero una cotización", "Tengo una pregunta", "Ahora no"],
        "ads": ["Sí, envíenme tarifas", "Tengo una pregunta", "Ahora no"],
        "social": ["Sí, envíenme tarifas", "Tengo una pregunta", "Ahora no"],
        "exit": ["Sí, envíenme tarifas", "Tengo una pregunta", "Ahora no"],
        "note": ["Quiero una cotización", "Tengo una pregunta"],
        "places": ["Europa", "Asia", "Otro lugar", "Tengo una pregunta"],
    },
    "fr": {
        "kayak": ["Oui, envoyez-moi les tarifs", "Modifier", "Pas maintenant"],
        "quote": ["Oui, envoyez-moi les tarifs", "J'ai une question", "Pas maintenant"],
        "quote_contact": ["Envoyez-moi les tarifs par e-mail", "J'ai une question", "Pas maintenant"],
        "reader": ["Obtenir un devis", "J'ai une question", "Pas maintenant"],
        "ads": ["Oui, envoyez-moi les tarifs", "J'ai une question", "Pas maintenant"],
        "social": ["Oui, envoyez-moi les tarifs", "J'ai une question", "Pas maintenant"],
        "exit": ["Oui, envoyez-moi les tarifs", "J'ai une question", "Pas maintenant"],
        "note": ["Obtenir un devis", "J'ai une question"],
        "places": ["Europe", "Asie", "Ailleurs", "J'ai une question"],
    },
}

# The trip line's own words (proactive._trip_line): months, travellers and cabins.
MONTHS: dict[str, tuple[str, ...]] = {
    "es": ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"),
    "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."),
}
PEOPLE: dict[str, dict[str, tuple[str, str]]] = {
    "es": {"adult": ("adulto", "adultos"), "child": ("niño", "niños"), "infant": ("bebé", "bebés")},
    "fr": {"adult": ("adulte", "adultes"), "child": ("enfant", "enfants"), "infant": ("bébé", "bébés")},
}
CABINS: dict[str, dict[str, str]] = {
    "es": {"economy": "clase Económica", "premium_economy": "Premium Economy", "business": "clase Business",
           "first": "Primera"},
    "fr": {"economy": "classe Économique", "premium_economy": "Premium Économique", "business": "classe Affaires",
           "first": "Première"},
}
# The cabin the fares are for, in the card's words (ADR A6, 20 Nov): what the visitor searched, business by
# default. Bing, 13-20 Nov: 685 of the 1,971 visitors on a destination page had typed "first class", and the
# card spoke of business class fares. A search for economy or premium economy is said as one too (CodeRabbit
# #272): the leads take economy, and an agent quotes what was asked for.
CABIN_FARES: dict[str, dict[str, str]] = {
    "en": {"business": "business class", "first": "first class", "premium_economy": "premium economy",
           "economy": "economy"},
    "es": {"business": "en clase Business", "first": "en Primera clase", "premium_economy": "en Premium Economy",
           "economy": "en clase Económica"},
    "fr": {"business": "en classe Affaires", "first": "en Première classe", "premium_economy": "en Premium Économique",
           "economy": "en classe Économique"},
}
# "one-way" after the dates (ADR A4): the site's form showed one-way searches as round trips.
ONE_WAY: dict[str, str] = {"en": "one-way", "es": "solo ida", "fr": "aller simple"}
# The cabin in the phone card's title (ADR A3): "Rome · First class".
TITLE_CABINS: dict[str, dict[str, str]] = {
    "en": {"economy": "Economy", "premium_economy": "Premium Economy", "business": "Business class",
           "first": "First class"},
    "es": {"economy": "Clase Económica", "premium_economy": "Premium Economy", "business": "Clase Business",
           "first": "Primera clase"},
    "fr": {"economy": "Classe Économique", "premium_economy": "Premium Économique", "business": "Classe Affaires",
           "first": "Première classe"},
}


def cabin_fares(lang: Optional[str], cabin: Optional[str]) -> str:
    """The searched cabin's fares in `lang` ("first class", "premium economy"…), "business class" when none."""
    words = CABIN_FARES.get(lang or "en", CABIN_FARES["en"])
    return words.get(cabin or "", words["business"])


# "4 adults in First": the word between the travellers and the cabin.
IN_CABIN: dict[str, str] = {"en": "in", "es": "en", "fr": "en"}


def sentence(lang: Optional[str], key: str) -> str:
    """The sentence `key` in `lang` (None: English)."""
    return SENTENCES.get(lang or "en", SENTENCES["en"])[key]


def replies(lang: Optional[str], key: str) -> Optional[list[str]]:
    """The buttons `key` in `lang`, or None for English (proactive._REPLIES holds those)."""
    if not lang:
        return None
    found = REPLIES.get(lang, {}).get(key)
    return list(found) if found else None
