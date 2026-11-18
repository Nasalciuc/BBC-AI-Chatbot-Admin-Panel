"""What follows the card (18 Nov 2026, "12 probleme Kayak și chat"): the instant answer to its yes and
the buttons WhatsApp / Email / "I'll wait here" (A1, A2, B2), the one message as a contactless client
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
        assert out["show"] and out["confirm"]["message"].startswith("Got it — New York → Paris")
        assert [b["kind"] for b in out["confirm"]["buttons"]] == ["whatsapp", "email", "wait", "change"]
        assert out["channel"] == {"kind": "whatsapp", "label": "Fares on WhatsApp"}
        assert not has_price(out["confirm"]["message"])

    def test_off_hours_there_is_no_waiting_here_and_a_computer_gets_email(self, monkeypatch):
        _on(monkeypatch)
        out = proactive.offer(_VISITOR, "kayak", _trip(), "bbc", device="computer", team_online=False)
        assert [b["kind"] for b in out["confirm"]["buttons"]] == ["whatsapp", "email", "change"]
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
        assert [b["kind"] for b in out["buttons"]] == ["whatsapp", "email"]
        assert "Hola" not in out["message"]  # the row is no greeting

    def test_the_rows_words_open_the_chat(self):
        assert proactive.applied_variant("quote", _trip(), proactive.ROW) == proactive.ROW
        assert proactive.known_variant("row") == "row" and proactive.known_variant("beat") == "beat"
        assert proactive.compose("quote", None, "bbc", proactive.ROW)[0] == (
            "Faster than the form: an agent can send you the fares on WhatsApp or by email.")

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

    def test_the_beat_sentence_starts_after_the_72_hours(self):
        v = self._beat_visitor()
        assert proactive.beat_variant(v, _trip(), _BEFORE) is None
        assert proactive.beat_variant(v, _trip(), _AFTER) == proactive.BEAT
        assert proactive.beat_variant(self._fares_visitor(), _trip(), _AFTER) is None

    def test_only_on_a_card_with_the_whole_trip_and_its_switch_stops_it(self, monkeypatch):
        v = self._beat_visitor()
        assert proactive.beat_variant(v, _trip(depart_date=None, return_date=None), _AFTER) is None
        monkeypatch.setenv("KAYAK_CARD_BEAT", "false")
        assert proactive.beat_variant(v, _trip(), _AFTER) is None

    def test_the_beat_words_name_no_price_and_its_yes_answer_asks_where(self):
        text, _ = proactive.compose("kayak", _trip(), "bbc", proactive.BEAT)
        assert "Already seen a price? An agent checks if they can beat it" in text
        assert not has_price(text)
        assert "beat the price you saw" in card_words.confirm("x", None, beat=True)

    def test_the_offer_carries_beat_for_its_side(self, monkeypatch):
        _on(monkeypatch)
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


def _buttons_patches(conv, users=0, agents=0, lead=None, online=()):
    saved = _Saved()
    counts = {"user": users, "agent": agents}
    lead = lead if lead is not None else {"id": "L1", "intent_signals": {}, "notes": None}
    return saved, [
        patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=conv)),
        patch("app.db.supabase.count_messages", new=AsyncMock(side_effect=lambda cid, role=None: counts.get(role, 0))),
        patch("app.services.conversation_service.add_message", new=saved),
        patch("app.services.lead_service.update_lead_from_entities", new=AsyncMock()),
        patch("app.services.lead_service.get_or_create_lead", new=AsyncMock(return_value=lead)),
        patch("app.db.supabase.update_lead", new=AsyncMock(return_value=True)),
        patch("app.services.routing.queue_for_person", new=AsyncMock(return_value=True)),
        patch("app.services.tasks_service.withdraw_call_tasks", new=AsyncMock(return_value=0)),
        patch("app.db.supabase.get_available_agents", new=AsyncMock(return_value=list(online))),
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
        assert out.message == saved.rows[1]["content"] and out.message.startswith("Got it — New York → Paris")
        assert out.message_id == "m2" and out.created_at == saved.rows[1]["created_at"]
        assert [b["kind"] for b in out.buttons] == ["whatsapp", "email", "wait", "change"]
        assert out.quick_replies == ["WhatsApp", "Email", "I'll wait here", "Change details"]
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
        assert ChatRequest(message="x", price_seen="  4,200 dollars  ").price_seen == "4,200 dollars"


class TestChannels:
    @pytest.mark.asyncio
    async def test_a_whatsapp_number_lands_with_its_channel_and_no_call(self):
        out, saved, m = await _tap(_req("whatsapp", "WhatsApp: +61 412 345 678"), _conv())
        m["entities"].assert_awaited_once_with(_CID, {"phone": "+61412345678", "_phone_country": None})
        written = m["update_lead"].await_args.args[1]
        assert written["intent_signals"]["contact_channel"] == "whatsapp"
        assert written["intent_signals"]["callback_requested"] is False
        assert written["notes"].startswith("CLIENT ASKED FOR WHATSAPP, NOT CALLS — +61412345678")
        assert m["queue"].await_args.kwargs["reason"] == "whatsapp request"
        assert out.message.startswith("Done — an agent will send your fares on WhatsApp to +61412345678 "
                                      "within about 30 minutes.")
        assert [r["role"] for r in saved.rows] == ["user", "ai"]
        assert not has_price(out.message)

    @pytest.mark.asyncio
    async def test_a_number_typed_the_local_way_is_placed_by_the_visits_country(self):
        out, _, m = await _tap(_req("whatsapp", "WhatsApp: 0412 345 678"), _conv({"tz": "Australia/Sydney"}))
        assert m["entities"].await_args.args[1]["phone"] == "+61412345678"

    @pytest.mark.asyncio
    async def test_an_email_lands_with_its_channel_and_the_price_they_saw(self):
        out, _, m = await _tap(_req("email", "Email: Client@Example.com", price_seen="4.2k"), _conv())
        m["entities"].assert_awaited_once_with(_CID, {"email": "client@example.com"})
        signals = m["update_lead"].await_args.args[1]["intent_signals"]
        assert signals["contact_channel"] == "email" and signals["price_seen"] == "$4,200"
        assert "notes" not in m["update_lead"].await_args.args[1]
        assert "$" not in out.message and "4,200" not in out.message  # never repeated to the client
        assert out.message.startswith("Done — an agent will email your fares to client@example.com")

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
    @pytest.mark.parametrize("button, message", [("whatsapp", "WhatsApp: soon"), ("email", "Email: client at example")])
    async def test_no_contact_in_it_goes_to_the_model(self, button, message):
        out, saved, m = await _tap(_req(button, message), _conv())
        assert out is None and saved.rows == []
        m["entities"].assert_not_awaited()


class TestWait:
    @pytest.mark.asyncio
    async def test_the_minute_is_promised_only_with_an_agent_online(self):
        out, _, m = await _tap(_req("wait", "I'll wait here"), _conv(), online=[{"id": "u1"}])
        assert out.message == "Great — an agent can join you here in about a minute." and out.buttons is None
        assert m["queue"].await_args.kwargs["reason"] == "waits in the chat"

    @pytest.mark.asyncio
    async def test_nobody_online_keeps_the_channels_offered(self):
        out, _, _ = await _tap(_req("wait", "I'll wait here"), _conv(), online=[])
        assert "as soon as one is free" in out.message and "minute" not in out.message
        assert [b["kind"] for b in out.buttons] == ["whatsapp", "email"]


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
        assert out["show"] and out["message"].startswith("Before you go — an agent can send you the fares for New York")
        assert [b["kind"] for b in out["buttons"]] == ["whatsapp", "email"]
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
        assert out["message"] == "Before you go — where should an agent send you the fares: WhatsApp or email?"


# ══════════════════════════════════════════════════════════════
# WhatsApp said in words
# ══════════════════════════════════════════════════════════════

class TestWhatsAppInWords:
    def test_heard_as_a_wish_that_owes_no_call(self):
        from app.pipeline.contact_capture import callback_channel
        from app.pipeline.entity_extractor import contact_channel_of, turns_down_calls

        assert contact_channel_of("On WhatsApp please") == "whatsapp"
        assert contact_channel_of("please don't whatsapp me") is None
        assert turns_down_calls("whatsapp")
        assert callback_channel("+1 210 555 0100", "number", "whatsapp") == "text"

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
              for k in ("whatsapp", "email") for lang in (None, "es", "fr") for online in (True, False)]
    every += [card_words.wait_answer(lang, online)[0] for lang in (None, "es", "fr") for online in (True, False)]
    every += [card_words.before_you_go(line, lang)[0] for lang in (None, "es", "fr") for line in (None, "Paris")]
    assert not [w for w in every if has_price(w)]
