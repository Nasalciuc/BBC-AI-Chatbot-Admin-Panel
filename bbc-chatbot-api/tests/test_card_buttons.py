"""What follows the card (18 Nov 2026, "12 probleme Kayak și chat"): the instant answer to its yes and
the buttons SMS / Email / "I'll wait here" (A1, A2, B2), the one message as a contactless client
leaves (B1), the row beside a refused quote form (D1), the card's own channel button (C1), the card in
Spanish and French (ADR-9b), and the two tests on the Kayak card (C4: its moment, C5: its words).

Every contact, route and price here is invented."""

import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.ai.output_guard import has_price
from app.api.chat import ChatInitRequest, ProactiveOfferRequest, proactive_offer
from app.models.chat import ChatRequest
from app.models.trip_context import TripContext
from app.pipeline import card_buttons
from app.services import card_words, proactive, proactive_i18n

_CID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_VISITOR = "visitor-0123456789"
_BEFORE = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
_AFTER = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def _in(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _trip(**over) -> TripContext:
    data = {"source": "kayak", "origin_code": "JFK", "destination_code": "CDG", "depart_date": _in(40),
            "return_date": _in(50), "trip_type": "round_trip", "cabin": "business", "adults": 2}
    data.update(over)
    return TripContext(**data)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    flags._reset_for_tests()
    for k in ("PROACTIVE_ENABLED", "PROACTIVE_PERCENT", "PROACTIVE_TRIGGERS", "KAYAK_CARD_BEAT", "KAYAK_EARLY_TEST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(proactive, "_c4_redis", None)
    monkeypatch.setattr(proactive, "_c4_redis_failed", True)  # memory only: no Redis in the suite
    monkeypatch.setattr(proactive, "C4_SHOWN", {"early": 0, "now": 0})
    saved = dict(card_buttons.CARD_BUTTONS_HEALTH)
    yield
    card_buttons.CARD_BUTTONS_HEALTH.clear()
    card_buttons.CARD_BUTTONS_HEALTH.update(saved)
    flags._reset_for_tests()


# ══════════════════════════════════════════════════════════════
# The card in Spanish and French (ADR-9b)
# ══════════════════════════════════════════════════════════════

class TestLanguages:
    @pytest.mark.parametrize("raw, lang", [("es", "es"), ("es-MX", "es"), ("fr-CA", "fr"), ("FR", "fr"),
                                           ("en-US", None), ("pt-BR", None), ("", None), (None, None), (7, None)])
    def test_the_card_speaks_spanish_and_french_only(self, raw, lang):
        assert proactive_i18n.card_lang(raw) == lang

    def test_english_is_word_for_word_what_it_was(self):
        text, replies = proactive.compose("kayak", _trip(), "bbc")
        assert text.startswith("Hi, I'm Alex from BuyBusinessClass. I see you're looking at New York → Paris")
        assert text.endswith("Want an agent to check private fares for exactly this trip?")
        assert replies == ["Yes, send me fares", "Change details", "Not now"]

    @pytest.mark.parametrize("lang, hello, yes, no, cabin", [
        ("es", "Hola, soy Alex, de BuyBusinessClass.", "Sí, envíenme tarifas", "Ahora no", "2 adultos en clase Business"),
        ("fr", "Bonjour, je suis Alex, de BuyBusinessClass.", "Oui, envoyez-moi les tarifs", "Pas maintenant",
         "2 adultes en classe Affaires"),
    ])
    def test_the_kayak_card_in_the_browsers_language(self, lang, hello, yes, no, cabin):
        text, replies = proactive.compose("kayak", _trip(), "bbc", None, lang)
        assert text.startswith(hello) and cabin in text
        assert replies[0] == yes and replies[-1] == no
        assert not has_price(text)

    def test_months_are_the_languages_own(self):
        trip = _trip(depart_date=None, return_date=None)
        d = date.today() + timedelta(days=40)
        line = proactive.trip_line(_trip(depart_date=d.isoformat(), return_date=None), "es")
        assert f"{d.day} {proactive_i18n.MONTHS['es'][d.month - 1]}" in line
        assert proactive.trip_line(trip, "fr").startswith("New York → Paris")

    @pytest.mark.parametrize("trigger, trip", [("quote", None), ("exit", None), ("reader", None), ("note", None),
                                               ("ads", "dest"), ("reader", "dest_date")])
    @pytest.mark.parametrize("lang", ["es", "fr"])
    def test_every_card_has_its_words_in_both_languages(self, trigger, trip, lang):
        t = {"dest": _trip(origin_code=None, depart_date=None, return_date=None),
             "dest_date": _trip(origin_code=None), None: None}[trip]
        text, replies = proactive.compose(trigger, t, "bbc", None, lang)
        english, _ = proactive.compose(trigger, t, "bbc")
        assert text and text != english and replies
        assert "Hi, I'm" not in text and not has_price(text)

    def test_the_places_buttons_too(self):
        _, replies = proactive.compose("note", None, "bbc", "places", "fr")
        assert replies == ["Europe", "Asie", "Ailleurs", "J'ai une question"]

    @pytest.mark.asyncio
    async def test_the_opening_is_written_in_the_cards_language_and_the_chat_keeps_it(self):
        trip = _trip()
        with patch("app.db.supabase.count_messages", new=AsyncMock(return_value=0)), \
             patch.object(proactive, "_claim_opening", new=AsyncMock(return_value=True)), \
             patch("app.services.conversation_service.add_message",
                   new=AsyncMock(return_value={"id": "m1", "content": "x", "created_at": "t"})) as add, \
             patch("app.db.supabase._merge_conv_metadata", new=AsyncMock()) as meta, \
             patch("app.services.proactive.seed_lead", new=AsyncMock()):
            await proactive.open_conversation(_CID, "kayak", trip, "bbc", None, "es")
        assert add.await_args.args[2] == proactive.compose("kayak", trip, "bbc", None, "es")[0]
        assert meta.await_args.args[1]["client_lang"] == "es"

    def test_init_takes_the_language_and_drops_anything_else(self):
        req = ChatInitRequest(proactive={"trigger": "kayak", "lang": "fr-FR"})
        assert req.proactive.lang == "fr"
        assert ChatInitRequest(proactive={"trigger": "kayak", "lang": "de"}).proactive.lang is None


class TestEnglishEndsTheCardsLanguage:
    def test_an_english_message_speaks_english(self):
        from app.pipeline.language import is_english, keep_lang, turn_lang

        assert is_english("I want to fly to Paris in March")
        assert not is_english("ok thanks")
        assert not is_english("Hola, I want to fly")
        assert turn_lang("I want to fly to Paris in March", "es") is None
        assert keep_lang("I want to fly to Paris in March", "es") == "en"
        assert keep_lang("+1 210 555 0100", "es") is None
        assert keep_lang("Bonjour", "es") == "fr"
        assert turn_lang("ok", "es") == "es"


# ══════════════════════════════════════════════════════════════
# The offer: what follows the yes (A2), the card's channel (C1), the row (D1)
# ══════════════════════════════════════════════════════════════

def _on(monkeypatch):
    monkeypatch.setenv("PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("PROACTIVE_PERCENT", "100")


class TestOffer:
    def test_a_card_with_the_whole_trip_carries_its_yes_answer_and_its_channel(self, monkeypatch):
        _on(monkeypatch)
        out = proactive.offer(_VISITOR, "kayak", _trip(), "bbc", device="phone", team_online=True)
        assert out["show"] and out["confirm"]["message"].startswith("Got it, New York → Paris")
        assert [b["kind"] for b in out["confirm"]["buttons"]] == ["sms", "email", "wait", "change"]
        assert out["channel"] == {"kind": "sms", "label": "Fares by text"}
        assert not has_price(out["confirm"]["message"])

    def test_off_hours_there_is_no_waiting_here_and_a_computer_gets_email(self, monkeypatch):
        _on(monkeypatch)
        out = proactive.offer(_VISITOR, "kayak", _trip(), "bbc", device="computer", team_online=False)
        assert [b["kind"] for b in out["confirm"]["buttons"]] == ["sms", "email", "change"]
        assert out["channel"]["kind"] == "email"

    @pytest.mark.parametrize("trip", [None, _trip(origin_code=None), _trip(depart_date=None, return_date=None)])
    def test_a_card_without_the_whole_trip_keeps_the_model(self, monkeypatch, trip):
        _on(monkeypatch)
        out = proactive.offer(_VISITOR, "reader", trip, "bbc", device="phone")
        assert "confirm" not in out and "channel" not in out

    def test_the_note_has_the_yes_answer_but_no_channel_of_its_own(self):
        out = proactive.offer(_VISITOR, "note", _trip(), "bbc", device="phone")
        assert "confirm" in out and "channel" not in out

    def test_the_quote_card_that_offers_the_chat_keeps_its_own_way(self, monkeypatch):
        _on(monkeypatch)
        out = proactive.offer(_VISITOR, "quote", _trip(), "bbc", refused=["email", "phone"])
        assert out["variant"] == proactive.CONTACT and "confirm" not in out

    def test_beside_a_refused_form_the_row_offers_both_channels(self, monkeypatch):
        _on(monkeypatch)
        out = proactive.offer(_VISITOR, "quote", _trip(), "bbc", refused=["email"], place="form", lang="es")
        assert out["variant"] == proactive.ROW and out["quick_replies"] == []
        assert out["message"].startswith("Más rápido que el formulario") and "New York → Paris" in out["message"]
        assert [b["kind"] for b in out["buttons"]] == ["sms", "email"]
        assert "Hola" not in out["message"]  # the row is no greeting
        assert out["lang"] == "es", "the opening the widget writes keeps the row's language (CodeRabbit #270)"

    def test_the_rows_words_open_the_chat(self):
        assert proactive.applied_variant("quote", _trip(), proactive.ROW) == proactive.ROW
        assert proactive.known_variant("row") == "row" and proactive.known_variant("beat") == "beat"
        assert proactive.compose("quote", None, "bbc", proactive.ROW)[0] == (
            "Faster than the form: an agent can send you the fares by text or by email.")

    @pytest.mark.asyncio
    async def test_the_endpoint_takes_the_new_fields_and_drops_bad_ones(self, monkeypatch):
        _on(monkeypatch)
        req = ProactiveOfferRequest(visitor_id=_VISITOR, trigger="kayak", trip=_trip(), site="bbc",
                                    lang="es-ES", device="tablet", place="anywhere", c4="sooner")
        assert (req.lang, req.device, req.place, req.c4) == ("es", None, None, None)
        out = await proactive_offer(req)
        assert out["lang"] == "es" and out["confirm"]["message"].startswith("Perfecto:")


# ══════════════════════════════════════════════════════════════
# C5: the Kayak card's second sentence, and C4: its moment
# ══════════════════════════════════════════════════════════════

class TestKayakTests:
    def _beat_visitor(self):
        from app.experiments import in_arm

        return next(f"visitor-{i:010d}" for i in range(500) if in_arm(proactive.BEAT_EXPERIMENT, f"visitor-{i:010d}", 50))

    def _fares_visitor(self):
        from app.experiments import in_arm

        return next(f"visitor-{i:010d}" for i in range(500)
                    if not in_arm(proactive.BEAT_EXPERIMENT, f"visitor-{i:010d}", 50))

    def test_the_usual_words_are_the_base_the_beat_sentence_runs_only_when_its_switch_says_so(self, monkeypatch):
        """C5 ended 23 Nov (as many taps, no more contacts): no visitor gets the beat sentence unless the owner runs
        the test again with KAYAK_CARD_BEAT=true."""
        v = self._beat_visitor()
        assert proactive.beat_variant(v, _trip(), _AFTER) is None
        monkeypatch.setenv("KAYAK_CARD_BEAT", "true")
        assert proactive.beat_variant(v, _trip(), _AFTER) == proactive.BEAT
        monkeypatch.setenv("KAYAK_CARD_BEAT", "false")
        assert proactive.beat_variant(v, _trip(), _AFTER) is None

    def test_by_default_the_offer_carries_the_usual_words(self, monkeypatch):
        _on(monkeypatch)
        with patch.object(proactive, "tests_started", return_value=True):
            out = proactive.offer(self._beat_visitor(), "kayak", _trip(), "bbc")
        assert "variant" not in out and "beat the price you saw" not in out["confirm"]["message"]
        assert "Already seen a price?" not in out["message"]

    def test_run_again_the_beat_sentence_starts_after_the_72_hours(self, monkeypatch):
        monkeypatch.setenv("KAYAK_CARD_BEAT", "true")
        v = self._beat_visitor()
        assert proactive.beat_variant(v, _trip(), _BEFORE) is None
        assert proactive.beat_variant(v, _trip(), _AFTER) == proactive.BEAT
        assert proactive.beat_variant(self._fares_visitor(), _trip(), _AFTER) is None

    def test_run_again_only_on_a_card_with_the_whole_trip(self, monkeypatch):
        monkeypatch.setenv("KAYAK_CARD_BEAT", "true")
        v = self._beat_visitor()
        assert proactive.beat_variant(v, _trip(depart_date=None, return_date=None), _AFTER) is None

    def test_the_beat_words_name_no_price_and_its_yes_answer_asks_where(self):
        text, _ = proactive.compose("kayak", _trip(), "bbc", proactive.BEAT)
        assert "Already seen a price? An agent checks if they can beat it" in text
        assert not has_price(text)
        assert "beat the price you saw" in card_words.confirm("x", None, beat=True)

    def test_run_again_the_offer_carries_beat_for_its_side(self, monkeypatch):
        _on(monkeypatch)
        monkeypatch.setenv("KAYAK_CARD_BEAT", "true")
        v = self._beat_visitor()
        with patch.object(proactive, "tests_started", return_value=True):
            out = proactive.offer(v, "kayak", _trip(), "bbc")
        assert out["variant"] == "beat" and "beat the price you saw" in out["confirm"]["message"]

    @pytest.mark.asyncio
    async def test_c4_runs_from_the_start_until_300_early_cards(self, monkeypatch):
        assert not await proactive.c4_running(_BEFORE)
        assert await proactive.c4_running(_AFTER)
        monkeypatch.setitem(proactive.C4_SHOWN, "early", proactive.C4_CAP)
        assert not await proactive.c4_running(_AFTER)

    @pytest.mark.asyncio
    async def test_its_switch_stops_it(self, monkeypatch):
        monkeypatch.setenv("KAYAK_EARLY_TEST", "false")
        assert not await proactive.c4_running(_AFTER)

    def test_an_early_card_after_the_test_waits_for_the_usual_moment(self, monkeypatch):
        _on(monkeypatch)
        out = proactive.offer(_VISITOR, "kayak", _trip(), "bbc", c4="early", c4_open=False)
        assert out == {"show": False, "arm": proactive.C4_HOLD, "trigger": "kayak", "message": None,
                       "quick_replies": []}
        assert proactive.offer(_VISITOR, "kayak", _trip(), "bbc", c4="now", c4_open=False)["show"]

    @pytest.mark.asyncio
    async def test_the_endpoint_counts_each_sides_cards(self, monkeypatch):
        _on(monkeypatch)
        with patch.object(proactive, "tests_started", return_value=True):
            for c4 in ("early", "early", "now"):
                await proactive_offer(ProactiveOfferRequest(visitor_id=_VISITOR, trigger="kayak", trip=_trip(), c4=c4))
        assert proactive.C4_SHOWN == {"early": 2, "now": 1}

    @pytest.mark.asyncio
    async def test_redis_holds_the_count_every_process_reads(self, monkeypatch):
        fake = MagicMock()
        fake.get = AsyncMock(return_value="301")
        fake.incr = AsyncMock()
        monkeypatch.setattr(proactive, "_c4_redis", fake)
        assert await proactive.c4_shown("early") == 301
        await proactive.c4_count("now")
        fake.incr.assert_awaited_once_with("proactive:c4:shown:now")


# ══════════════════════════════════════════════════════════════
# The buttons after the card (app.pipeline.card_buttons)
# ══════════════════════════════════════════════════════════════

def _conv(meta=None, **over):
    base = {"id": _CID, "status": "active", "mode": "ai", "visitor_email": None, "visitor_phone": None,
            "metadata": {"proactive_trigger": "kayak", "proactive_trip": _trip().model_dump(mode="json"),
                         "site": "bbc", **(meta or {})}}
    base.update(over)
    return base


def _req(button, message, **over):
    return ChatRequest(message=message, conversation_id=_CID, visitor_id=_VISITOR, button=button, **over)


class _Saved:
    """conversation_service.add_message: every saved row, with an id and a time."""

    def __init__(self):
        self.rows = []

    async def __call__(self, cid, role, content, model_used=None, cost=0.0, live_extra=None):
        row = {"id": f"m{len(self.rows) + 1}", "role": role, "content": content,
               "created_at": f"2026-11-18T10:00:0{len(self.rows)}+00:00", "model_used": model_used}
        self.rows.append(row)
        return row


def _buttons_patches(conv, users=0, agents=0, lead=None, online=(), saves=True):
    saved = _Saved()
    counts = {"user": users, "agent": agents}
    lead = lead if lead is not None else {"id": "L1", "intent_signals": {}, "notes": None}
    return saved, [
        patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=conv)),
        patch("app.db.supabase.count_messages", new=AsyncMock(side_effect=lambda cid, role=None: counts.get(role, 0))),
        patch("app.services.conversation_service.add_message", new=saved),
        patch("app.services.lead_service.update_lead_from_entities", new=AsyncMock(return_value=saves)),
        patch("app.services.lead_service.get_or_create_lead", new=AsyncMock(return_value=lead)),
        patch("app.db.supabase.update_lead", new=AsyncMock(return_value=True)),
        patch("app.services.routing.queue_for_person", new=AsyncMock(return_value=True)),
        patch("app.services.tasks_service.withdraw_call_tasks", new=AsyncMock(return_value=0)),
        patch("app.db.supabase.get_available_agents", new=AsyncMock(return_value=list(online))),
        patch("app.db.supabase.get_recent_messages", new=AsyncMock(return_value=[])),
        patch("app.services.closing.is_within_team_hours", return_value=True),
    ]


async def _tap(req, conv, **kw):
    saved, ps = _buttons_patches(conv, **kw)
    started = [p.start() for p in ps]
    try:
        out = await card_buttons.answer(req, req.message, {})
    finally:
        for p in ps:
            p.stop()
    names = ["conv", "count", "add", "entities", "lead", "update_lead", "queue", "withdraw", "agents", "hours"]
    return out, saved, dict(zip(names, started))


class TestYes:
    @pytest.mark.asyncio
    async def test_the_yes_is_answered_at_once_with_the_trip_and_the_buttons(self):
        out, saved, m = await _tap(_req("yes", "Yes, send me fares"), _conv())
        assert [r["role"] for r in saved.rows] == ["user", "ai"]
        assert saved.rows[1]["model_used"] == "proactive_template"
        assert out.message == saved.rows[1]["content"] and out.message.startswith("Got it, New York → Paris")
        assert out.message_id == "m2" and out.created_at == saved.rows[1]["created_at"]
        assert [b["kind"] for b in out.buttons] == ["sms", "email", "wait", "change"]
        assert out.quick_replies == ["Text message", "Email", "I'll wait here", "Change details"]
        assert out.type == "template" and out.streaming is False
        assert card_buttons.CARD_BUTTONS_HEALTH["yes"] == 1

    @pytest.mark.asyncio
    async def test_in_spanish_when_the_card_was(self):
        out, _, _ = await _tap(_req("yes", "Sí, envíenme tarifas"), _conv({"client_lang": "es"}))
        assert out.message.startswith("Perfecto: New York → Paris") and "Correo" in out.quick_replies

    @pytest.mark.asyncio
    async def test_the_beat_side_asks_about_the_price_they_saw(self):
        out, _, _ = await _tap(_req("yes", "Yes, send me fares"), _conv({"proactive_variant": "beat"}))
        assert "beat the price you saw" in out.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize("conv, users", [
        (_conv(), 1),                                                  # not the first answer
        (_conv({"proactive_trip": {"destination_code": "CDG"}}), 0),  # the card did not know the trip
        (_conv({"proactive_trigger": None}), 0),                       # no card opened this chat
    ])
    async def test_otherwise_the_model_answers(self, conv, users):
        out, saved, _ = await _tap(_req("yes", "Yes, send me fares"), conv, users=users)
        assert out is None and saved.rows == []
        assert card_buttons.CARD_BUTTONS_HEALTH["fell_through"] == 1

    def test_an_unknown_button_is_dropped_never_a_422(self):
        assert ChatRequest(message="x", button="call").button is None
        assert ChatRequest(message="x", button="sms").button == "sms"
        assert ChatRequest(message="x", button="whatsapp").button == "whatsapp"  # a widget cached from before
        assert ChatRequest(message="x", price_seen="  4,200 dollars  ").price_seen == "4,200 dollars"


class TestChannels:
    @pytest.mark.asyncio
    async def test_an_sms_number_lands_as_a_text_wish_and_no_call(self):
        """The SMS button (20 Nov: SMS in place of WhatsApp): the lead's wish is "text" — the one a client who
        types "text me" has, which the panel shows and the consultant's line names — and no call is owed."""
        out, saved, m = await _tap(_req("sms", "Text message: +61 412 345 678"), _conv())
        m["entities"].assert_awaited_once_with(_CID, {"phone": "+61412345678", "_phone_country": None})
        written = m["update_lead"].await_args.args[1]
        assert written["intent_signals"]["contact_channel"] == "text"
        assert written["intent_signals"]["callback_requested"] is False
        assert written["notes"].startswith("CLIENT ASKED FOR TEXTS, NOT CALLS — +61412345678")
        assert m["queue"].await_args.kwargs["reason"] == "sms request"
        m["withdraw"].assert_called_once_with(_CID)
        assert out.message.startswith("Done, an agent will text your fares to +61412345678 within about 30 minutes.")
        assert "WhatsApp" not in out.message
        assert [r["role"] for r in saved.rows] == ["user", "ai"]
        assert card_buttons.CARD_BUTTONS_HEALTH["sms"] == 1
        assert not has_price(out.message)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("lang, start", [("es", "Listo: un agente le enviará las tarifas por SMS al +61412345678"),
                                             ("fr", "C'est noté : un agent vous enverra les tarifs par SMS au +61412345678")])
    async def test_the_sms_answer_in_the_cards_language(self, lang, start):
        out, _, _ = await _tap(_req("sms", "SMS: +61 412 345 678"), _conv({"client_lang": lang}))
        assert out.message.startswith(start)

    @pytest.mark.asyncio
    async def test_an_earlier_no_texts_gives_way_to_the_sms_tapped_now(self):
        lead = {"id": "L1", "intent_signals": {"contact_channel": "no texts, no calls"}, "notes": None}
        _, _, m = await _tap(_req("sms", "Text message: +61 412 345 678"), _conv(), lead=lead)
        assert m["update_lead"].await_args.args[1]["intent_signals"]["contact_channel"] == "no calls, text"

    @pytest.mark.asyncio
    async def test_an_sms_tapped_after_whatsapp_is_the_wish_a_later_bare_number_follows(self):
        """CodeRabbit #272: "whatsapp, text" kept WhatsApp first; the latest of the two is the wish."""
        from app.pipeline.contact_capture import callback_channel

        lead = {"id": "L1", "intent_signals": {"contact_channel": "whatsapp"}, "notes": None}
        _, _, m = await _tap(_req("sms", "Text message: +61 412 345 678"), _conv(), lead=lead)
        stored = m["update_lead"].await_args.args[1]["intent_signals"]["contact_channel"]
        assert stored == "text"
        assert callback_channel("+61 412 345 678", "number", stored) == "text"

    @pytest.mark.asyncio
    async def test_the_wish_not_written_is_written_again_once_before_done(self):
        """CodeRabbit #272: the number is kept; only the wish is written again, and "Done" follows. Both tries
        hold the same lock (round 3): no other choice is written between them, and the lead is read again."""
        from app.services import lead_service

        real_lock = lead_service._lead_lock
        held, entries, exits = [], [], []

        class _Counted:
            """The conversation's real lock, each entry and exit counted (round 4: one hold, not two)."""

            def __init__(self, cid):
                self.lock = real_lock(cid)

            async def __aenter__(self):
                entries.append(1)
                return await self.lock.__aenter__()

            async def __aexit__(self, *exc):
                exits.append(1)
                return await self.lock.__aexit__(*exc)

        async def update_lead(lead_id, payload):
            held.append(real_lock(_CID).locked())
            return None if len(held) == 1 else {"id": lead_id}

        saved, ps = _buttons_patches(_conv())
        ps[5] = patch("app.db.supabase.update_lead", new=AsyncMock(side_effect=update_lead))
        ps.append(patch.object(lead_service, "_lead_lock", new=_Counted))
        started = [p.start() for p in ps]
        try:
            out = await card_buttons.answer(_req("sms", "Text message: +61 412 345 678"),
                                            "Text message: +61 412 345 678", {})
        finally:
            for p in ps:
                p.stop()
        assert out.message.startswith("Done, an agent will text your fares to +61412345678")
        assert started[5].await_count == 2 and started[3].await_count == 1      # the number once
        assert held == [True, True] and started[4].await_count == 2              # held, the lead read again
        assert len(entries) == 1 and len(exits) == 1                             # one hold for both tries
        assert [r["role"] for r in saved.rows] == ["user", "ai"]                # the message once
        assert card_buttons.CARD_BUTTONS_HEALTH["channel_not_noted"] == 0

    @pytest.mark.asyncio
    async def test_a_wish_not_written_twice_is_counted_and_the_client_still_answered(self, caplog):
        _, ps = _buttons_patches(_conv())
        ps[5] = patch("app.db.supabase.update_lead", new=AsyncMock(return_value=None))
        started = [p.start() for p in ps]
        try:
            with caplog.at_level(logging.ERROR):
                out = await card_buttons.answer(_req("email", "Email: client@example.com"),
                                                "Email: client@example.com", {})
        finally:
            for p in ps:
                p.stop()
        assert out is not None and started[5].await_count == 2
        assert card_buttons.CARD_BUTTONS_HEALTH["channel_not_noted"] == 1
        assert "client@example.com" not in caplog.text                          # no PII in the log

    @pytest.mark.asyncio
    async def test_a_whatsapp_tap_from_a_widget_cached_from_before_stays_whatsapp(self):
        """A widget cached from before SMS (a day at most) still offers WhatsApp: that client typed the number
        into a field that said WhatsApp, and is answered and noted as such (never "will text you")."""
        out, saved, m = await _tap(_req("whatsapp", "WhatsApp: +61 412 345 678"), _conv())
        m["entities"].assert_awaited_once_with(_CID, {"phone": "+61412345678", "_phone_country": None})
        written = m["update_lead"].await_args.args[1]
        assert written["intent_signals"]["contact_channel"] == "whatsapp"
        assert written["intent_signals"]["callback_requested"] is False
        assert written["notes"].startswith("CLIENT ASKED FOR WHATSAPP, NOT CALLS — +61412345678")
        assert m["queue"].await_args.kwargs["reason"] == "whatsapp request"
        m["withdraw"].assert_called_once_with(_CID)
        assert out.message.startswith("Done, an agent will send your fares on WhatsApp to +61412345678 "
                                      "within about 30 minutes.")
        assert [r["role"] for r in saved.rows] == ["user", "ai"]

    @pytest.mark.asyncio
    async def test_a_number_typed_the_local_way_is_placed_by_the_visits_country(self):
        out, _, m = await _tap(_req("sms", "Text message: 0412 345 678"), _conv({"tz": "Australia/Sydney"}))
        assert m["entities"].await_args.args[1]["phone"] == "+61412345678"

    @pytest.mark.asyncio
    async def test_an_email_lands_with_its_channel_and_the_price_they_saw(self):
        out, _, m = await _tap(_req("email", "Email: Client@Example.com", price_seen="4.2k"), _conv())
        m["entities"].assert_awaited_once_with(_CID, {"email": "client@example.com"})
        signals = m["update_lead"].await_args.args[1]["intent_signals"]
        assert signals["contact_channel"] == "email" and signals["price_seen"] == "$4,200"
        assert "notes" not in m["update_lead"].await_args.args[1]
        assert "$" not in out.message and "4,200" not in out.message  # never repeated to the client
        assert out.message.startswith("Done, an agent will email your fares to client@example.com")

    @pytest.mark.asyncio
    async def test_without_a_trip_the_answer_asks_for_it(self):
        """The row beside the quote form: an email, no trip on the page or the lead (#3355). The
        answer asks where and when, never for the extras of a trip nobody said."""
        conv = _conv({"proactive_trigger": "quote", "proactive_trip": None})
        out, _, _ = await _tap(_req("email", "Email: client@example.com"), conv, lead={"id": "L1"})
        assert out.message.startswith("Done, an agent will email your fares to client@example.com")
        assert out.message.endswith("Where would you like to fly, and roughly when? Write it here.")
        assert "flexible dates" not in out.message

    @pytest.mark.asyncio
    async def test_a_trip_on_the_lead_keeps_the_extras_question(self):
        conv = _conv({"proactive_trigger": "quote", "proactive_trip": None})
        out, _, _ = await _tap(_req("email", "Email: client@example.com"), conv,
                               lead={"id": "L1", "destination_code": "LHR"})
        assert out.message.endswith("Anything they should know, like flexible dates or an airline you prefer? "
                                    "Write it here.")

    def test_the_trip_question_in_each_language(self):
        assert card_words.channel_done("email", "a@b.co", "es", True, trip_known=False).endswith(
            "¿A dónde quiere volar y, más o menos, cuándo? Escríbalo aquí.")
        assert card_words.channel_done("whatsapp", "+33612345678", "fr", False, trip_known=False).endswith(
            "Où souhaitez-vous aller, et à peu près quand ? Écrivez-le ici.")

    @pytest.mark.asyncio
    async def test_off_hours_the_promise_is_the_vaguer_one(self):
        saved, ps = _buttons_patches(_conv())
        ps[-1] = patch("app.services.closing.is_within_team_hours", return_value=False)
        for p in ps:
            p.start()
        try:
            out = await card_buttons.answer(_req("email", "Email: client@example.com"), "Email: client@example.com", {})
        finally:
            for p in ps:
                p.stop()
        assert "first thing when our team is back" in out.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize("button, message", [("sms", "Text message: soon"), ("whatsapp", "WhatsApp: soon"),
                                                 ("email", "Email: client at example")])
    async def test_no_contact_in_it_goes_to_the_model(self, button, message):
        out, saved, m = await _tap(_req(button, message), _conv())
        assert out is None and saved.rows == []
        m["entities"].assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("button, message", [("sms", "Text message: +61 412 345 678"),
                                                 ("email", "Email: client@example.com")])
    async def test_a_contact_not_saved_is_never_said_done(self, button, message):
        """The write failed: no "Done, an agent will…", no queue, no message saved here. The model's turn
        takes the message, saves it and tries the contact again (CodeRabbit #270)."""
        out, saved, m = await _tap(_req(button, message), _conv(), saves=False)
        assert out is None and saved.rows == []
        m["entities"].assert_awaited_once()
        m["queue"].assert_not_awaited()
        m["update_lead"].assert_not_awaited()
        assert card_buttons.CARD_BUTTONS_HEALTH["contact_not_saved"] == 1
        assert card_buttons.CARD_BUTTONS_HEALTH["fell_through"] == 1

    @pytest.mark.asyncio
    async def test_the_channel_is_noted_under_the_lead_writers_lock(self):
        """The read, the merge and the write of intent_signals happen while lead_service's lock for this
        chat is held: a model turn's write waits instead of crossing it (CodeRabbit #270)."""
        from app.services import lead_service

        held = []

        async def update_lead(lead_id, payload):
            held.append(lead_service._lead_lock(_CID).locked())
            return {"id": lead_id}

        saved, ps = _buttons_patches(_conv())
        ps[5] = patch("app.db.supabase.update_lead", new=AsyncMock(side_effect=update_lead))
        for p in ps:
            p.start()
        try:
            out = await card_buttons.answer(_req("email", "Email: client@example.com"), "Email: client@example.com", {})
        finally:
            for p in ps:
                p.stop()
        assert out is not None and held == [True]
        assert not lead_service._lead_lock(_CID).locked()


class TestWait:
    @pytest.mark.asyncio
    async def test_the_minute_is_promised_only_with_an_agent_online(self):
        out, _, m = await _tap(_req("wait", "I'll wait here"), _conv(), online=[{"id": "u1"}])
        assert out.message == "Great, an agent can join you here in about a minute." and out.buttons is None
        assert m["queue"].await_args.kwargs["reason"] == "waits in the chat"

    @pytest.mark.asyncio
    async def test_nobody_online_keeps_the_channels_offered(self):
        out, _, _ = await _tap(_req("wait", "I'll wait here"), _conv(), online=[])
        assert "as soon as one is free" in out.message and "minute" not in out.message
        assert "tap Text message or Email" in out.message
        assert [b["kind"] for b in out.buttons] == ["sms", "email"]


# ══════════════════════════════════════════════════════════════
# B1: one message as a client with no contact leaves
# ══════════════════════════════════════════════════════════════

class TestBeforeYouGo:
    async def _ask(self, conv, users=1, agents=0, claim=True):
        saved, ps = _buttons_patches(conv, users=users, agents=agents)
        ps.append(patch.object(card_buttons, "_claim", new=AsyncMock(return_value=claim)))
        for p in ps:
            p.start()
        try:
            return await card_buttons.before_you_go(_CID), saved
        finally:
            for p in ps:
                p.stop()

    @pytest.mark.asyncio
    async def test_once_with_the_trip_and_the_buttons(self):
        out, saved = await self._ask(_conv())
        assert out["show"] and out["message"].startswith("Before you go, an agent can send you the fares for New York")
        assert [b["kind"] for b in out["buttons"]] == ["sms", "email"]
        assert out["id"] == "m1" and saved.rows[0]["role"] == "ai"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("conv, users, agents, claim, why", [
        (_conv(visitor_email="client@example.com"), 1, 0, True, "has_contact"),
        (_conv(visitor_phone="+12105550100"), 1, 0, True, "has_contact"),
        (_conv(), 1, 1, True, "agent_wrote"),
        (_conv(), 0, 0, True, "never_wrote"),
        (_conv(status="closed"), 1, 0, True, "closed"),
        (_conv({"before_you_go_at": "2026-11-18T10:00:00+00:00"}), 1, 0, True, "already_said"),
        (_conv(), 1, 0, False, "already_said"),
    ])
    async def test_never_with_a_contact_an_agent_or_twice(self, conv, users, agents, claim, why):
        out, saved = await self._ask(conv, users=users, agents=agents, claim=claim)
        assert out == {"show": False, "why": why} and saved.rows == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ago_s, last, shows", [
        (30, "Denver to Athens, flexible — what's the best email for them to reach you?", False),
        (30, "Where should they write: WhatsApp or email?", False),
        (30, "And a number for your consultant — would a text or a quick call work better?", False),
        (180, "What's the best email for them to reach you?", True),     # asked long ago: the client left
        (30, "Email noted. When are you looking to travel?", True),      # the email named, not asked
    ])
    async def test_not_over_alexs_own_ask_for_the_contact(self, ago_s, last, shows):
        """30 s after "what's the best email for them to reach you?", with the client still in the chat,
        the before-you-go asked the same again (#3359, 19 Nov). Under two minutes after Alex asked for an
        email, a WhatsApp or a number, it is not said."""
        said = (datetime.now(timezone.utc) - timedelta(seconds=ago_s)).isoformat()
        saved, ps = _buttons_patches(_conv(), users=1)
        # One patch per name: two on the same one, stopped in start order, leave a mock behind.
        ps = [p for p in ps if getattr(p, "attribute", None) != "get_recent_messages"]
        ps.append(patch.object(card_buttons, "_claim", new=AsyncMock(return_value=True)))
        ps.append(patch("app.db.supabase.get_recent_messages",
                        new=AsyncMock(return_value=[{"role": "ai", "content": last, "created_at": said}])))
        for p in ps:
            p.start()
        try:
            out = await card_buttons.before_you_go(_CID)
        finally:
            for p in ps:
                p.stop()
        assert out["show"] is shows
        if not shows:
            assert out == {"show": False, "why": "just_asked"} and saved.rows == []

    @pytest.mark.parametrize("created_at", ["2026-11-19T10:00:00", None, "yesterday"])
    def test_a_time_it_cannot_read_lets_it_go_on(self, created_at):
        """A zoneless, missing or unreadable time is no recent ask: the before-you-go goes on, as before."""
        recent = [{"role": "ai", "content": "What's the best email for them to reach you?", "created_at": created_at}]
        assert card_buttons._just_asked_contact(recent) is False

    @pytest.mark.asyncio
    async def test_the_trip_the_client_changed_to_is_the_one_said(self):
        """The page searched Paris; the client then said London: the lead's trip is the one said back
        (CodeRabbit #270). The page's trip stays for a lead that has none (the test above)."""
        lead = {"id": "L1", "origin_code": "JFK", "destination_code": "LHR", "departure_date": _in(40),
                "return_date": _in(47), "passengers": 2}
        saved, ps = _buttons_patches(_conv(), users=1, lead=lead)
        ps.append(patch.object(card_buttons, "_claim", new=AsyncMock(return_value=True)))
        for p in ps:
            p.start()
        try:
            out = await card_buttons.before_you_go(_CID)
        finally:
            for p in ps:
                p.stop()
        assert "London" in out["message"] and "Paris" not in out["message"]

    @pytest.mark.asyncio
    async def test_a_destination_alone_on_the_lead_is_the_one_said(self):
        """The client changed the destination before giving any origin: the lead holds London only, and
        London is named, never the page's Paris (CodeRabbit #270, round 2)."""
        saved, ps = _buttons_patches(_conv(), users=1, lead={"id": "L1", "destination_code": "LHR"})
        ps.append(patch.object(card_buttons, "_claim", new=AsyncMock(return_value=True)))
        for p in ps:
            p.start()
        try:
            out = await card_buttons.before_you_go(_CID)
        finally:
            for p in ps:
                p.stop()
        assert "London" in out["message"] and "Paris" not in out["message"]

    @pytest.mark.asyncio
    async def test_without_a_trip_it_still_asks_where_to_write(self):
        conv = _conv({"proactive_trip": None})
        saved, ps = _buttons_patches(conv, users=1, lead={"id": "L1"})
        ps.append(patch.object(card_buttons, "_claim", new=AsyncMock(return_value=True)))
        for p in ps:
            p.start()
        try:
            out = await card_buttons.before_you_go(_CID)
        finally:
            for p in ps:
                p.stop()
        assert out["message"] == "Before you go, where should an agent send you the fares: by text or by email?"


# ══════════════════════════════════════════════════════════════
# WhatsApp said in words (still heard: the client names the app; we offer SMS)
# ══════════════════════════════════════════════════════════════

class TestWhatsAppInWords:
    def test_heard_as_a_wish_that_owes_no_call(self):
        from app.pipeline.contact_capture import callback_channel
        from app.pipeline.entity_extractor import contact_channel_of, turns_down_calls

        assert contact_channel_of("On WhatsApp please") == "whatsapp"
        assert contact_channel_of("please don't whatsapp me") is None
        assert turns_down_calls("whatsapp")
        # WhatsApp stays WhatsApp when the number comes later: never "ASKED FOR TEXTS" (CodeRabbit #270).
        assert callback_channel("+1 210 555 0100", "number", "whatsapp") == "whatsapp"
        assert callback_channel("+1 210 555 0100", "number", "no texts, whatsapp") == "whatsapp"
        assert callback_channel("whatsapp me +1 210 555 0100", "number", None) == "whatsapp"
        assert callback_channel("whatsapp me +1 210 555 0100", "number", "text") == "whatsapp"
        # "Call me" said now wins over the older WhatsApp; texts stay texts.
        assert callback_channel("call me +1 210 555 0100", "words", "whatsapp") == "call"
        assert callback_channel("+1 210 555 0100", "number", "text") == "text"

    @pytest.mark.asyncio
    async def test_a_number_after_the_whatsapp_button_is_a_whatsapp_number(self):
        """The client chose WhatsApp, then typed the number alone: the note, the reply and the queue say
        WhatsApp, and no call is owed (CodeRabbit #270)."""
        from tests.test_chat_contacts import _callback_turn, _lead_payloads

        run, mocks = _callback_turn("+1 210 555 0100", lead_signals={"contact_channel": "whatsapp"},
                                    conv_extra={"visitor_email": "cliente@example.com"})
        resp = await run()
        # The template as written; the client reads it after the output guard (db.add_message, mocked here).
        assert resp.message.startswith("Got it — your consultant will write to you on WhatsApp at +12105550100.")
        payload = _lead_payloads(mocks)[-1]
        assert payload["intent_signals"]["contact_channel"] == "whatsapp"
        assert payload["intent_signals"]["callback_requested"] is False
        assert payload["notes"].startswith("CLIENT ASKED FOR WHATSAPP, NOT CALLS — +12105550100")
        assert mocks["queue_for_person"].await_args.kwargs["reason"] == "whatsapp request"
        mocks["task_from_callback"].assert_not_called()

    def test_the_whatsapp_reply_speaks_the_clients_language(self):
        from app.ai.templates import get_template
        from app.models.chat import VisitorInfo
        from app.pipeline.language import current_lang

        for code, start in (("es", "Perfecto: su consultor le escribirá por WhatsApp al +1"),
                            ("fr", "Parfait : votre conseiller vous écrira sur WhatsApp au +1"),
                            ("pt", "Perfeito: o seu consultor vai escrever pelo WhatsApp para o +1")):
            token = current_lang.set(code)
            try:
                assert get_template("number_confirmed_whatsapp", "sales", VisitorInfo(), number="+1").startswith(start)
            finally:
                current_lang.reset(token)

    def test_the_consultants_line_and_only_the_newest_wish(self):
        from app.pipeline.callback_intent import callback_note, notes_with_callback

        note = callback_note("+12105550100", channel="whatsapp")
        assert note == "CLIENT ASKED FOR WHATSAPP, NOT CALLS — +12105550100"
        older = "CLIENT ASKED TO BE CALLED — +12105550100\nRoute JFK → CDG"
        assert notes_with_callback(older, note) == f"{note}\nRoute JFK → CDG"
        assert notes_with_callback(f"{note}\nx", callback_note("+1", channel="text")).count("WHATSAPP") == 0


def test_no_button_word_names_an_amount():
    every = [card_words.confirm("New York → Paris", lang, beat) for lang in (None, "es", "fr") for beat in (False, True)]
    every += [card_words.channel_done(k, "+12105550100", lang, online)
              for k in ("sms", "whatsapp", "email") for lang in (None, "es", "fr") for online in (True, False)]
    every += [card_words.wait_answer(lang, online)[0] for lang in (None, "es", "fr") for online in (True, False)]
    every += [card_words.before_you_go(line, lang)[0] for lang in (None, "es", "fr") for line in (None, "Paris")]
    every += [card_words.goodbye_without_contact(lang) for lang in (None, "es", "fr")]
    assert not [w for w in every if has_price(w)]


@pytest.mark.parametrize("lang", [None, "es", "fr"])
def test_nothing_we_offer_names_whatsapp_any_more(monkeypatch, lang):
    """20 Nov, the owner: SMS in place of WhatsApp everywhere. Every word the card and the buttons after it
    offer — the yes answer, the buttons, the card's own channel, the pill, the row beside the form, the
    wait, the before-you-go, the tested Kayak sentence — says SMS (or text) and email, in every language.
    The WhatsApp answer stays only for a tap from a widget cached from before (test above)."""
    _on(monkeypatch)
    every: list[str] = []
    for beat in (False, True):
        every.append(card_words.confirm("New York → Paris", lang, beat))
    for online in (True, False):
        every += [b["label"] for b in card_words.confirm_buttons(lang, online)]
        text, buttons = card_words.wait_answer(lang, online)
        every += [text, *(b["label"] for b in buttons)]
        every.append(card_words.channel_done("sms", "+12105550100", lang, online))
    for line in (None, "New York → Paris"):
        text, buttons = card_words.before_you_go(line, lang)
        every += [text, *(b["label"] for b in buttons)]
    every += [b["label"] for b in card_words.row_buttons(lang)]
    every += [card_words.card_channel(card_words.device_channel(d), lang)["label"] for d in ("phone", "computer")]
    for variant in (None, proactive.BEAT):
        every.append(proactive.compose("kayak", _trip(), "bbc", variant, lang)[0])
    every.append(proactive.compose("quote", _trip(), "bbc", proactive.ROW, lang)[0])
    every.append(proactive.compose("quote", None, "bbc", proactive.ROW, lang)[0])
    out = proactive.offer(_VISITOR, "kayak", _trip(), "bbc", device="phone", team_online=True, lang=lang)
    every += [out["message"], out["confirm"]["message"], out["channel"]["label"],
              *(b["label"] for b in out["confirm"]["buttons"])]
    assert every and not [w for w in every if "whatsapp" in w.lower()]
    # …and none of them has the long dash (20 Nov: it reads as a machine's; tests/test_no_long_dash.py).
    assert not [w for w in every if "—" in w]
    assert any("SMS" in w or "text" in w for w in every)
    assert card_words.device_channel("phone") == "sms" and "whatsapp" not in card_words.KINDS


# ══════════════════════════════════════════════════════════════
# The once-only claim keeps what others wrote (CodeRabbit #270)
# ══════════════════════════════════════════════════════════════

class _Row:
    """One conversations row and the PostgREST filters _claim uses, evaluated like Postgres would."""

    def __init__(self, metadata, change_after_read=None):
        self.metadata = metadata
        self.change_after_read = change_after_read  # what another request writes between a read and the update
        self.reads = 0
        self.updates = []

    def table(self, _name):
        return _Query(self)


class _Query:
    def __init__(self, row):
        self.row, self.op, self.payload, self.ok = row, None, None, True

    def select(self, *_a):
        self.op = "select"
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, *_a):
        return self

    def single(self):
        return self

    def is_(self, column, _null):
        if column == "metadata":
            self.ok = self.ok and self.row.metadata is None
        else:
            key = column.split("->>")[1]
            self.ok = self.ok and not (self.row.metadata or {}).get(key)
        return self

    def contains(self, _column, seen):
        import json as _json

        self.ok = self.ok and self.row.metadata is not None and all(
            self.row.metadata.get(k) == v for k, v in _json.loads(seen).items())
        return self

    def contained_by(self, _column, seen):
        import json as _json

        theirs = _json.loads(seen)
        self.ok = self.ok and self.row.metadata is not None and all(
            theirs.get(k) == v for k, v in self.row.metadata.items())
        return self

    def execute(self):
        if self.op == "select":
            self.row.reads += 1
            data = {"metadata": None if self.row.metadata is None else dict(self.row.metadata)}
            if self.row.change_after_read and self.row.reads == 1:
                self.row.metadata = {**(self.row.metadata or {}), **self.row.change_after_read}
            return MagicMock(data=data)
        self.row.updates.append(self.payload)
        if not self.ok:
            return MagicMock(data=[])
        self.row.metadata = self.payload["metadata"]
        return MagicMock(data=[{"id": _CID}])


async def _claim_on(row):
    with patch("app.db.supabase.get_client", return_value=row), \
         patch("app.db.supabase._run_sync", new=AsyncMock(side_effect=lambda fn, **k: fn())):
        return await card_buttons._claim(_CID, card_buttons.BEFORE_YOU_GO_KEY)


class TestClaim:
    @pytest.mark.asyncio
    async def test_a_presence_ping_in_between_is_kept(self):
        """The widget says the client is leaving while the claim runs: that write lands between the read
        and the update. The update misses, the claim reads again, and both are kept."""
        row = _Row({"client_lang": "en"}, change_after_read={"presence": "left"})
        assert await _claim_on(row) is True
        assert row.metadata["presence"] == "left" and row.metadata["client_lang"] == "en"
        assert row.metadata[card_buttons.BEFORE_YOU_GO_KEY]
        assert row.reads == 2 and len(row.updates) == 2

    @pytest.mark.asyncio
    async def test_once(self):
        row = _Row({"client_lang": "en"})
        assert await _claim_on(row) is True
        assert await _claim_on(row) is False

    @pytest.mark.asyncio
    async def test_another_request_claimed_it_in_between(self):
        row = _Row({}, change_after_read={card_buttons.BEFORE_YOU_GO_KEY: "2026-11-18T10:00:00+00:00"})
        assert await _claim_on(row) is False
        assert row.metadata[card_buttons.BEFORE_YOU_GO_KEY] == "2026-11-18T10:00:00+00:00"

    @pytest.mark.asyncio
    async def test_a_chat_without_metadata_can_be_claimed(self):
        row = _Row(None)
        assert await _claim_on(row) is True
        assert list(row.metadata) == [card_buttons.BEFORE_YOU_GO_KEY]

    @pytest.mark.asyncio
    async def test_a_key_another_request_removed_is_not_brought_back(self):
        """The snapshot held a key another request then removed: the update misses (@> fails) and the
        claim reads again, so the removed key stays removed (CodeRabbit #270, round 2)."""

        class _Removing(_Row):
            def table(self, _name):
                q = _Query(self)
                if self.reads == 1 and "widget_open" in (self.metadata or {}):
                    self.metadata = {k: v for k, v in self.metadata.items() if k != "widget_open"}
                return q

        row = _Removing({"client_lang": "en", "widget_open": True})
        assert await _claim_on(row) is True
        assert "widget_open" not in row.metadata and row.metadata["client_lang"] == "en"
        assert row.metadata[card_buttons.BEFORE_YOU_GO_KEY]

    @pytest.mark.asyncio
    async def test_metadata_too_long_to_compare_is_not_claimed(self, monkeypatch):
        """Past _CLAIM_EQUAL_MAX the claim is not made at all — never written under a weaker condition."""
        monkeypatch.setattr(card_buttons, "_CLAIM_EQUAL_MAX", 10)
        row = _Row({"client_lang": "en", "site": "bbc"})
        assert await _claim_on(row) is False
        assert row.updates == [] and card_buttons.BEFORE_YOU_GO_KEY not in row.metadata
        assert card_buttons.CARD_BUTTONS_HEALTH["before_you_go_too_long"] == 1

    @pytest.mark.asyncio
    async def test_metadata_that_never_settles_is_not_claimed(self):
        class _Busy(_Row):
            def table(self, _name):
                self.metadata = {**(self.metadata or {}), "ping": self.reads}
                return _Query(self)

        row = _Busy({"client_lang": "en"})
        assert await _claim_on(row) is False
        assert card_buttons.BEFORE_YOU_GO_KEY not in row.metadata


# ══════════════════════════════════════════════════════════════
# Only the conversation's visitor writes to it (CodeRabbit #270)
# ══════════════════════════════════════════════════════════════

class _PastTheCheck(Exception):
    """Raised by the next step after the owner check: the request got through it."""


class TestOwner:
    async def _send(self, conv, visitor_id):
        from fastapi import HTTPException

        from app.api import chat as chat_api

        http = MagicMock()
        http.headers = {}
        http.client = MagicMock(host="127.0.0.1")
        req = ChatRequest(message="Email: client@example.com", conversation_id=_CID, visitor_id=visitor_id,
                          button="email")
        entities = AsyncMock(return_value=True)
        with patch("app.api.chat.db.count_messages", new=AsyncMock(return_value=1)), \
             patch("app.api.chat.db.get_conversation_simple", new=AsyncMock(return_value=conv)), \
             patch("app.api.chat.is_blocked", new=AsyncMock(side_effect=[False, _PastTheCheck()])), \
             patch("app.services.lead_service.update_lead_from_entities", new=entities):
            try:
                await chat_api.chat(req, http, None)
            except HTTPException as e:
                return e.status_code, entities
            except _PastTheCheck:
                return "through", entities
        return "answered", entities

    @pytest.mark.asyncio
    @pytest.mark.parametrize("visitor_id", ["visitor-someone-else", None])
    async def test_another_visitor_or_none_is_refused_before_anything_is_written(self, visitor_id):
        from app.api.chat import CHAT_OWNER_HEALTH

        before = CHAT_OWNER_HEALTH["refused"]
        status, entities = await self._send(_conv(visitor_id=_VISITOR), visitor_id)
        assert status == 403
        entities.assert_not_awaited()
        assert CHAT_OWNER_HEALTH["refused"] == before + 1

    @pytest.mark.asyncio
    async def test_the_owner_and_a_chat_without_a_visitor_go_through(self):
        assert (await self._send(_conv(visitor_id=_VISITOR), _VISITOR))[0] == "through"
        assert (await self._send(_conv(visitor_id=None), "visitor-anyone"))[0] == "through"
