"""A client who writes Spanish, French or Portuguese (chat #3325, 18 Nov 2026).

What broke there, in order: English templates took over at the phone number; the name question asked
in Spanish was not heard; "Español Por Favor" was stored as the name; "mensaje de texto" was not read
as a wish for texts, so a call task was made; and an English summary was re-asked until the client
left. Every case here is invented: no real client's words, route or contact."""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.ai.templates import build_summary, get_template
from app.models.chat import VisitorInfo
from app.pipeline.contact_capture import asks_for_name, callback_channel, channel_from_answer, name_from_answer
from app.pipeline.language import current_lang, detect_lang, turn_lang
from app.pipeline.orchestrator import _normalize_reply, is_confirmation, is_rejection


@pytest.fixture
def lang():
    tokens = []

    def _set(value):
        tokens.append(current_lang.set(value))

    yield _set
    for t in reversed(tokens):
        current_lang.reset(t)


@pytest.mark.parametrize("text, expected", [
    ("Hola, quiero un vuelo a Madrid", "es"),
    ("Español por favor", "es"),
    ("mensaje de texto", "es"),
    ("Buenas tardes, necesito precio", "es"),
    ("Bonjour, je ne parle pas anglais", "fr"),
    ("Merci beaucoup", "fr"),
    ("Olá, quero uma passagem", "pt"),
    ("Obrigado", "pt"),
])
def test_a_message_that_says_its_language(text, expected):
    assert detect_lang(text) == expected


@pytest.mark.parametrize("text", [
    "I want to fly JFK to LHR", "Buenos Aires", "Flights to Buenos Aires please", "Mon Dec 10", "ok", "sí",
    "AI?", "Can you do me a favor", "+1 555 010 0100", "someone@example.com", "texto", "por favor",
])
def test_english_and_messages_that_say_nothing_are_no_language(text):
    assert detect_lang(text) is None


def test_the_language_stays_once_a_message_said_it():
    assert turn_lang("+1 555 010 0100", "es") == "es"
    assert turn_lang("Bonjour", "es") == "fr"
    assert turn_lang("ok", None) is None
    assert turn_lang("ok", "de") is None  # only the languages the replies speak


@pytest.mark.parametrize("q", ["¡Perfecto! ¿Cuál es su nombre completo?", "¿Cómo se llama?",
                               "Et quel est votre nom ?", "Qual é o seu nome?", "Como você se chama?"])
def test_the_name_question_in_spanish_french_portuguese_is_heard(q):
    assert asks_for_name(q)


@pytest.mark.parametrize("answer", ["Español Por Favor", "español", "Inglés no", "merci", "Obrigado", "por favor"])
def test_a_language_or_a_courtesy_is_never_a_name(answer):
    assert name_from_answer(answer) is None


@pytest.mark.parametrize("answer, name", [("María José", "María José"), ("Jean-Luc", "Jean-Luc"), ("joão", "João")])
def test_names_with_accents_still_count(answer, name):
    assert name_from_answer(answer) == name


@pytest.mark.parametrize("answer", ["sim", "Sim.", "si", "Claro!", "vale", "Perfeito"])
def test_a_bare_yes_is_never_a_name(answer):
    """"Sim" to "your name?" agreed to something; it is not the client's name (CodeRabbit #270)."""
    assert name_from_answer(answer) is None


@pytest.mark.parametrize("answer, name", [("Sim Lee", "Sim Lee"), ("Simão", "Simão"), ("Simone", "Simone")])
def test_names_that_begin_like_a_yes_still_count(answer, name):
    assert name_from_answer(answer) == name


def test_dale_is_a_name_in_english_and_a_yes_in_spanish(lang):
    """"Dale" answers "your name?" in an English chat; in a Spanish one it agrees (CodeRabbit #270, round 3)."""
    assert name_from_answer("Dale") == "Dale"
    assert name_from_answer("dale") == "Dale"
    lang("es")
    assert name_from_answer("Dale") is None and name_from_answer("dale") is None
    assert name_from_answer("Dale Cooper") == "Dale Cooper"


@pytest.mark.parametrize("message", ["+55 11 99999-0000 por mensagem", "+55 11 99999-0000 por mensagens"])
def test_a_number_with_mensagem_is_a_text_not_a_call(message):
    """The singular too: "mensagens?" matched "mensagen", never "mensagem" (CodeRabbit #270)."""
    assert callback_channel(message, "number", None) == "text"


@pytest.mark.parametrize("answer, channel", [
    ("mensaje de texto", "text"), ("por mensaje, por favor", "text"), ("no me llamen, mensaje por favor", "text"),
    ("llámame", "call"), ("par message svp", "text"), ("pas d'appel, un message", "text"), ("appelez-moi", "call"),
    ("mensagem de texto", "text"), ("pode me ligar", "call"), ("por correo", "email"), ("par courriel", "email"),
])
def test_the_channel_in_spanish_french_portuguese(answer, channel):
    assert channel_from_answer(answer) == channel


def test_a_number_given_with_a_spanish_wish_for_texts_is_a_text_not_a_call():
    assert callback_channel("+1 555 010 0100 mensaje de texto", "number", None) == "text"


def test_templates_speak_the_turns_language_and_fall_back_to_english(lang):
    assert get_template("number_confirmed_text", "sales", VisitorInfo(), number="+15550100") == \
        "Got it — your consultant will text you on +15550100."
    lang("es")
    assert get_template("number_confirmed_text", "sales", VisitorInfo(), number="+15550100") == \
        "Perfecto: su consultor le escribirá al +15550100."
    assert get_template("ask_name", "sales", VisitorInfo()).startswith("¿Y su nombre")
    assert get_template("closing", "sales", VisitorInfo())  # not translated: the English one, not None


_LEAD = {"origin_code": "JFK", "destination_code": "MAD", "departure_date": "2027-02-10",
         "return_date": "2027-02-20", "passengers": 2, "cabin_class": "business"}


@pytest.mark.parametrize("code, word, answer", [("es", "ida y vuelta", "SÍ"), ("fr", "aller-retour", "OUI"),
                                                ("pt", "ida e volta", "SIM")])
def test_the_summary_speaks_the_language_and_its_yes_confirms(lang, code, word, answer):
    lang(code)
    summary = build_summary(_LEAD, extra_legs=[{"from": "MAD", "to": "LIS", "date": "2027-02-15"}])
    assert word in summary and answer in summary
    assert "MAD → LIS" in summary.split(answer)[0], "the extra leg comes before the question"
    assert is_confirmation(_normalize_reply(answer))


@pytest.mark.parametrize("reply", ["Sí, correcto", "sim", "C'est bon", "Tudo certo", "D'accord", "perfecto"])
def test_yes_in_spanish_french_portuguese_confirms(reply):
    assert is_confirmation(_normalize_reply(reply))


@pytest.mark.parametrize("reply", [
    "Perfeito, mas…", "Sim, porém um adulto", "Certo, contudo", "Sí, salvo la fecha", "Oui, sauf le retour",
    "D'accord, pourtant",
])
def test_a_yes_followed_by_a_correction_is_not_a_yes(reply):
    """"Perfeito, mas…" corrects the summary: confirming it would send a wrong lead (CodeRabbit #270)."""
    assert not is_confirmation(_normalize_reply(reply))


def test_a_plain_perfeito_still_confirms():
    assert is_confirmation(_normalize_reply("Perfeito"))
    assert is_confirmation(_normalize_reply("Perfeito!"))


@pytest.mark.parametrize("reply", ["não", "errado", "faux", "pas correct"])
def test_no_in_spanish_french_portuguese_rejects(reply):
    assert is_rejection(_normalize_reply(reply))


@pytest.mark.parametrize("text, wish", [
    ("mensaje de texto", "text"), ("Por mensaje por favor, no me llamen", "text, no calls"),
    ("par message svp", "text"), ("mensagem de texto", "text"), ("por correo electrónico", "email"),
    ("par courriel", "email"), ("pas d'appel", "no calls"),
])
def test_the_extractor_hears_a_channel_wish_in_spanish_french_portuguese(text, wish):
    from app.pipeline.entity_extractor import contact_channel_of

    assert contact_channel_of(text) == wish


@pytest.mark.parametrize("text, wish", [
    ("No quiero mensajes de texto", "no texts"), ("sin SMS, por favor", "no texts"),
    ("no me manden correos", "no email"), ("pas de SMS", "no texts"), ("pas d'e-mails", "no email"),
    ("ne m'envoyez pas de messages", "no texts"), ("não quero mensagens", "no texts"),
    ("sem e-mail", "no email"),
])
def test_a_channel_turned_down_in_spanish_french_portuguese_is_a_refusal_not_a_wish(text, wish):
    """"No quiero mensajes de texto" turns texts down: read as a wish for texts, the consultant would
    text the one client who said not to (CodeRabbit #270)."""
    from app.pipeline.entity_extractor import contact_channel_of

    assert contact_channel_of(text) == wish


@pytest.mark.asyncio
async def test_a_spanish_client_who_chose_texts_is_answered_in_spanish_with_no_call_task():
    """The #3325 order: "mensaje de texto" first (kept as the wish), then the number alone."""
    from tests.test_chat_contacts import _CID, _callback_turn

    run, mocks = _callback_turn("+1 210 555 0100", lead_signals={"contact_channel": "text"},
                                conv_extra={"visitor_email": "cliente@example.com", "metadata": {"client_lang": "es"}})
    with patch("app.pipeline.orchestrator.db.patch_conversation_presence", new=AsyncMock(return_value=True)) as patch_meta:
        resp = await run()
    assert resp.message.startswith("Perfecto: su consultor le escribirá al +12105550100")
    assert "While they get to you" not in resp.message
    mocks["task_from_callback"].assert_not_called()
    assert mocks["queue_for_person"].await_args.kwargs["reason"] == "text request"
    patch_meta.assert_not_awaited()  # the language was already on the conversation


@pytest.mark.asyncio
async def test_the_language_a_message_says_is_kept_on_the_conversation():
    from tests.test_chat_contacts import _CID, _callback_turn

    run, mocks = _callback_turn("Hola, llámame al +1 210 555 0100", conv_extra={"visitor_email": "cliente@example.com"})
    with patch("app.pipeline.orchestrator.db.patch_conversation_presence", new=AsyncMock(return_value=True)) as patch_meta:
        resp = await run()
    patch_meta.assert_any_await(_CID, {"client_lang": "es"})
    assert resp.message.startswith("Por supuesto: un consultor le llamará al +12105550100")
