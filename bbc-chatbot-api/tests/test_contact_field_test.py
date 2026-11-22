"""The owner's contact test (22 Nov 2026, app.services.contact_field_test): on the field side the card's yes opens the
contact field at once with words that ask for that one contact, and "Get a fare quote" on a card that knows no whole
trip is answered at once, contact first; the choice side, and a widget cached from before, are as they were.

Every contact, route and visitor here is invented."""

import os
import sys
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

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
from app.services import card_words, contact_field_test, proactive

_CID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


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
    for k in ("PROACTIVE_ENABLED", "PROACTIVE_PERCENT", "PROACTIVE_TRIGGERS", "KAYAK_CARD_BEAT",
              "KAYAK_EARLY_TEST", "CONTACT_FIELD_TEST_PERCENT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("PROACTIVE_PERCENT", "100")
    monkeypatch.setenv("KAYAK_CARD_BEAT", "false")  # the C5 sentence only where a test asks for it
    monkeypatch.setattr(proactive, "_c4_redis", None)
    monkeypatch.setattr(proactive, "_c4_redis_failed", True)
    saved_buttons = dict(card_buttons.CARD_BUTTONS_HEALTH)
    saved_sides = dict(contact_field_test.CONTACT_FIELD_HEALTH)
    yield
    card_buttons.CARD_BUTTONS_HEALTH.clear()
    card_buttons.CARD_BUTTONS_HEALTH.update(saved_buttons)
    contact_field_test.CONTACT_FIELD_HEALTH.clear()
    contact_field_test.CONTACT_FIELD_HEALTH.update(saved_sides)
    flags._reset_for_tests()


def _visitor(side: str) -> str:
    """A visitor id on `side` at the owner's 50."""
    for i in range(500):
        vid = f"visitor-{i:010d}"
        if contact_field_test.side(vid) == side:
            return vid
    raise AssertionError(f"no visitor on the {side} side")


@pytest.fixture
def field_v():
    return _visitor(contact_field_test.FIELD)


@pytest.fixture
def choice_v():
    return _visitor(contact_field_test.CHOICE)


# ══════════════════════════════════════════════════════════════
# The side
# ══════════════════════════════════════════════════════════════

class TestSide:
    def test_half_the_visitors_each_side_and_a_visitor_keeps_theirs(self, field_v, choice_v):
        assert contact_field_test.percent() == 50
        assert contact_field_test.side(field_v) == "field" and contact_field_test.side(field_v) == "field"
        assert contact_field_test.side(choice_v) == "choice"
        sides = [contact_field_test.side(f"visitor-{i:010d}") for i in range(400)]
        assert 140 < sides.count("field") < 260, "about half"

    def test_salted_by_its_own_name_not_the_agent_first_tests_halves(self):
        from app.experiments import bucket
        ids = [f"visitor-{i:010d}" for i in range(200)]
        assert [bucket("contact_field", v) for v in ids] != [bucket("agent_first", v) for v in ids]

    def test_0_ends_it_for_everyone_and_an_unreadable_switch_keeps_the_owners_50(self, monkeypatch, field_v):
        monkeypatch.setenv("CONTACT_FIELD_TEST_PERCENT", "0")
        flags._reset_for_tests()
        assert contact_field_test.side(field_v) is None
        monkeypatch.setenv("CONTACT_FIELD_TEST_PERCENT", "nonsense")  # app.flags warns and keeps the default
        flags._reset_for_tests()
        assert contact_field_test.percent() == 50 and contact_field_test.side(field_v) == "field"

    def test_no_visitor_id_no_side(self):
        assert contact_field_test.side(None) is None and contact_field_test.side("") is None

    def test_the_chat_takes_the_side_it_was_told_only_when_it_is_the_visitors(self, field_v, choice_v):
        meta: dict = {}
        contact_field_test.tag(meta, field_v, "field", "sms")
        assert meta == {"contact_test": "field", "contact_field": "sms"}
        meta = {}
        contact_field_test.tag(meta, choice_v, "choice", "sms")
        assert meta == {"contact_test": "choice"}, "a field on the choice side is not believed"
        for vid, said in ((choice_v, "field"), (field_v, "choice"), (field_v, None)):
            meta = {}
            contact_field_test.tag(meta, vid, said, "email")
            assert meta == {}, (said, "a side the server did not give, or a widget cached from before")

    def test_the_field_the_chat_opened(self):
        assert contact_field_test.field_of({"contact_test": "field", "contact_field": "email"}) == "email"
        assert contact_field_test.field_of({"contact_test": "choice", "contact_field": "email"}) is None
        assert contact_field_test.field_of({"contact_test": "field", "contact_field": "fax"}) is None
        assert contact_field_test.field_of({"contact_test": "field"}) is None
        assert contact_field_test.field_of(None) is None


# ══════════════════════════════════════════════════════════════
# The card's answer (/chat/proactive/offer)
# ══════════════════════════════════════════════════════════════

class TestOffer:
    def test_on_the_field_side_the_yes_opens_the_phones_field_and_asks_its_number(self, field_v):
        out = proactive.offer(field_v, "kayak", _trip(), "bbc", device="phone", team_online=True, opens_field=True)
        assert out["contact_test"] == "field"
        assert out["confirm"]["field"] == "sms"
        assert out["confirm"]["message"].startswith("Got it, New York → Paris")
        assert out["confirm"]["message"].endswith("and text them to you. What's your mobile number?")
        assert [b["kind"] for b in out["confirm"]["buttons"]] == ["sms", "email", "wait", "change"]
        assert out["channel"] == {"kind": "sms", "label": "Fares by text"}
        assert contact_field_test.CONTACT_FIELD_HEALTH["field"] == 1

    def test_on_a_computer_the_email_field(self, field_v):
        out = proactive.offer(field_v, "kayak", _trip(), "bbc", device="computer", opens_field=True)
        assert out["confirm"]["field"] == "email"
        assert out["confirm"]["message"].endswith("and email them to you. What's your email address?")

    def test_the_choice_side_is_as_it_was(self, choice_v):
        before = proactive.offer(choice_v, "kayak", _trip(), "bbc", device="phone", team_online=True)
        out = proactive.offer(choice_v, "kayak", _trip(), "bbc", device="phone", team_online=True, opens_field=True)
        assert out["contact_test"] == "choice" and "field" not in out["confirm"]
        assert out["confirm"] == before["confirm"]
        assert out["confirm"]["message"].endswith("Where should they write: by text or by email?")
        assert contact_field_test.CONTACT_FIELD_HEALTH["choice"] == 1, "only the answer that took part counts"

    def test_a_widget_cached_from_before_is_on_neither_side(self, field_v):
        out = proactive.offer(field_v, "kayak", _trip(), "bbc", device="phone", team_online=True)
        assert "contact_test" not in out and "field" not in out["confirm"]
        assert out["confirm"]["message"].endswith("Where should they write: by text or by email?")

    @pytest.mark.parametrize("trigger, trip", [("reader", None), ("note", TripContext(destination_code="CDG"))])
    def test_get_a_fare_quote_with_no_trip_is_answered_contact_first_on_the_field_side(self, field_v, trigger, trip):
        out = proactive.offer(field_v, trigger, trip, "bbc", device="phone", team_online=True, opens_field=True)
        assert out["contact_test"] == "field"
        assert out["quick_replies"][0] == "Get a fare quote"
        assert out["confirm"] == {
            "message": "Sure. An agent will check live fares for your trip and text them to you. What's your mobile number?",
            "buttons": [card_words.button(k) for k in ("sms", "email", "wait")],
            "field": "sms",
        }

    @pytest.mark.parametrize("trigger, trip", [("reader", None), ("note", TripContext(destination_code="CDG"))])
    def test_on_the_choice_side_the_model_asks_the_trip_as_before(self, choice_v, trigger, trip):
        out = proactive.offer(choice_v, trigger, trip, "bbc", device="phone", opens_field=True)
        assert out["contact_test"] == "choice" and "confirm" not in out

    @pytest.mark.parametrize("variant", ["open", "places"])
    def test_the_notes_bandit_question_is_left_as_it_is(self, monkeypatch, field_v, variant):
        """With no destination on the page the note asks its own first question (a region to tap, or where and
        when typed) and its bandit learns from the visitor's next message: the test changes nothing there."""
        monkeypatch.setattr(proactive.note_bandit, "choose", lambda: variant)
        out = proactive.offer(field_v, "note", None, "bbc", device="phone", opens_field=True)
        assert out["variant"] == variant
        assert "contact_test" not in out and "confirm" not in out

    def test_the_ads_card_with_a_place_and_no_dates_keeps_its_channel_and_gets_the_answer(self, field_v):
        trip = TripContext(destination_code="CDG")
        out = proactive.offer(field_v, "ads", trip, "bbc", device="computer", team_online=False, opens_field=True)
        assert out["confirm"]["field"] == "email"
        assert out["confirm"]["message"].endswith("What's your email address?")
        assert [b["kind"] for b in out["confirm"]["buttons"]] == ["sms", "email"], "no waiting here off hours"
        assert out["channel"]["kind"] == "email"

    def test_a_card_the_test_changes_nothing_on_is_not_named(self, field_v):
        # A Kayak card that knows part of the trip: no instant answer on either side.
        part = proactive.offer(field_v, "kayak", _trip(depart_date=None, return_date=None), "bbc", opens_field=True)
        assert "contact_test" not in part and "confirm" not in part
        # The quote card that offers the chat instead of the form keeps its own way.
        contact = proactive.offer(field_v, "quote", _trip(), "bbc", refused=["email", "phone"], opens_field=True)
        assert contact["variant"] == proactive.CONTACT and "contact_test" not in contact

    def test_the_beat_sentence_asks_for_the_fields_contact_too(self, monkeypatch, field_v):
        monkeypatch.setattr(proactive, "beat_variant", lambda vid, trip: proactive.BEAT)
        out = proactive.offer(field_v, "kayak", _trip(), "bbc", device="phone", opens_field=True)
        assert out["confirm"]["message"].endswith("beat the price you saw, and text you. What's your mobile number?")

    @pytest.mark.parametrize("lang, start, sms, email", [
        ("es", "Perfecto:", "¿Cuál es su número de móvil?", "¿Cuál es su correo?"),
        ("fr", "Parfait :", "Quel est votre numéro de mobile ?", "Quelle est votre adresse e-mail ?"),
    ])
    def test_in_spanish_and_french(self, field_v, lang, start, sms, email):
        phone = proactive.offer(field_v, "kayak", _trip(), "bbc", lang=lang, device="phone", opens_field=True)
        computer = proactive.offer(field_v, "kayak", _trip(), "bbc", lang=lang, device="computer", opens_field=True)
        assert phone["confirm"]["message"].startswith(start) and phone["confirm"]["message"].endswith(sms)
        assert computer["confirm"]["message"].endswith(email)
        reader = proactive.offer(field_v, "reader", None, "bbc", lang=lang, device="phone", opens_field=True)
        assert reader["confirm"]["message"].endswith(sms)

    @pytest.mark.parametrize("lang", [None, "es", "fr"])
    def test_no_amount_and_no_long_dash_in_any_of_its_words(self, lang):
        line = "New York → Paris, 2 adults"
        for field in ("sms", "email"):
            for beat in (False, True):
                text = card_words.confirm(line, lang, beat=beat, field=field)
                assert not has_price(text) and "—" not in text
            first = card_words.contact_first(lang, field)
            assert not has_price(first) and "—" not in first

    def test_a_field_that_is_not_one_is_the_usual_words_or_email(self):
        assert card_words.confirm("X", None, field="fax") == card_words.confirm("X", None)
        assert card_words.contact_first(None, "fax") == card_words.contact_first(None, "email")

    @pytest.mark.asyncio
    async def test_the_endpoint_takes_part_only_for_a_widget_that_says_so(self, field_v):
        req = ProactiveOfferRequest(visitor_id=field_v, trigger="kayak", trip=_trip(), site="bbc", device="phone",
                                    opens_field=True)
        assert req.opens_field is True
        assert (await proactive_offer(req))["confirm"]["field"] == "sms"
        for odd in ("yes", 1, "true", None):
            assert ProactiveOfferRequest(visitor_id=field_v, trigger="kayak", opens_field=odd).opens_field is False
        old = ProactiveOfferRequest(visitor_id=field_v, trigger="kayak", trip=_trip(), site="bbc", device="phone")
        assert "contact_test" not in await proactive_offer(old)

    def test_init_takes_the_side_and_the_field_and_drops_anything_else(self):
        ok = ChatInitRequest(proactive={"trigger": "kayak", "contact_test": "field", "field": "sms"})
        assert (ok.proactive.contact_test, ok.proactive.field) == ("field", "sms")
        odd = ChatInitRequest(proactive={"trigger": "kayak", "contact_test": "agent", "field": "fax"})
        assert (odd.proactive.contact_test, odd.proactive.field) == (None, None)


# ══════════════════════════════════════════════════════════════
# /chat/init: the side lands with the opening's label
# ══════════════════════════════════════════════════════════════

class TestOpening:
    @pytest.mark.asyncio
    async def test_the_side_is_written_with_the_label_only_on_a_chat_the_card_opened(self):
        with patch("app.db.supabase.count_messages", new=AsyncMock(return_value=0)), \
             patch.object(proactive, "_claim_opening", new=AsyncMock(return_value=True)), \
             patch("app.services.conversation_service.add_message",
                   new=AsyncMock(return_value={"id": "m1", "content": "x", "created_at": "t"})), \
             patch("app.db.supabase._merge_conv_metadata", new=AsyncMock()) as meta, \
             patch("app.services.proactive.seed_lead", new=AsyncMock()):
            await proactive.open_conversation(_CID, "kayak", _trip(), "bbc", None, None,
                                              contact={"contact_test": "field", "contact_field": "sms"})
        written = meta.await_args.args[1]
        assert written["proactive_trigger"] == "kayak"
        assert (written["contact_test"], written["contact_field"]) == ("field", "sms")

    @pytest.mark.asyncio
    async def test_a_chat_with_messages_already_gets_neither(self):
        with patch("app.db.supabase.count_messages", new=AsyncMock(return_value=3)), \
             patch("app.db.supabase._merge_conv_metadata", new=AsyncMock()) as meta:
            out = await proactive.open_conversation(_CID, "kayak", _trip(), "bbc", contact={"contact_test": "field"})
        assert out is None
        meta.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_init_hands_the_checked_side_to_the_opening(self, field_v, choice_v):
        from unittest.mock import MagicMock

        from app.api.chat import chat_init

        req = MagicMock()
        req.headers = {}
        req.client = MagicMock(host="127.0.0.1")
        for vid, said, expected in (
            (field_v, "field", {"contact_test": "field", "contact_field": "email"}),
            (choice_v, "field", None),       # not this visitor's side: not believed
            (field_v, None, None),           # a widget cached from before
        ):
            payload = ChatInitRequest(visitor_id=vid, proactive={"trigger": "kayak", "contact_test": said,
                                                                 "field": "email"})
            with patch("app.api.chat.is_blocked", new=AsyncMock(return_value=False)), \
                 patch("app.services.conversation_service.get_or_create_conversation",
                       new=AsyncMock(return_value={"id": _CID})), \
                 patch("app.services.proactive.open_conversation",
                       new=AsyncMock(return_value={"content": "the opening", "created_at": "t"})) as opener:
                await chat_init(payload, req, None)
            assert opener.await_args.kwargs["contact"] == expected, said


# ══════════════════════════════════════════════════════════════
# The yes, answered at once (app.pipeline.card_buttons)
# ══════════════════════════════════════════════════════════════

def _conv(meta=None):
    return {"id": _CID, "status": "active", "mode": "ai", "visitor_email": None, "visitor_phone": None,
            "metadata": {"proactive_trigger": "kayak", "proactive_trip": _trip().model_dump(mode="json"),
                         "site": "bbc", **(meta or {})}}


def _reader(meta=None):
    return {"id": _CID, "status": "active", "mode": "ai", "visitor_email": None, "visitor_phone": None,
            "metadata": {"proactive_trigger": "reader", "site": "bbc", **(meta or {})}}


_FIELD_SMS = {"contact_test": "field", "contact_field": "sms"}


class _Saved:
    def __init__(self):
        self.rows = []

    async def __call__(self, cid, role, content, model_used=None, cost=0.0, live_extra=None):
        row = {"id": f"m{len(self.rows) + 1}", "role": role, "content": content,
               "created_at": f"2026-11-22T10:00:0{len(self.rows)}+00:00", "model_used": model_used}
        self.rows.append(row)
        return row


async def _yes(conv, message, users=0, team=True):
    saved = _Saved()
    req = ChatRequest(message=message, conversation_id=_CID, visitor_id="visitor-0000000001", button="yes")
    with patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=conv)), \
         patch("app.db.supabase.count_messages", new=AsyncMock(return_value=users)), \
         patch("app.services.conversation_service.add_message", new=saved), \
         patch("app.services.closing.is_within_team_hours", return_value=team):
        out = await card_buttons.answer(req, message, {})
    return out, saved


class TestYes:
    @pytest.mark.asyncio
    async def test_the_field_sides_yes_stores_the_words_that_ask_for_its_contact(self):
        out, saved = await _yes(_conv(_FIELD_SMS), "Yes, send me fares")
        assert [r["role"] for r in saved.rows] == ["user", "ai"], "the tap is saved first"
        assert out.message == saved.rows[1]["content"]
        assert out.message.startswith("Got it, New York → Paris")
        assert out.message.endswith("What's your mobile number?")
        assert [b["kind"] for b in out.buttons] == ["sms", "email", "wait", "change"]
        assert card_buttons.CARD_BUTTONS_HEALTH["yes"] == 1

    @pytest.mark.asyncio
    async def test_the_choice_sides_yes_is_as_it_was(self):
        out, _ = await _yes(_conv({"contact_test": "choice"}), "Yes, send me fares")
        assert out.message.endswith("Where should they write: by text or by email?")

    @pytest.mark.asyncio
    async def test_get_a_fare_quote_with_no_trip_is_answered_contact_first_on_the_field_side(self):
        out, saved = await _yes(_reader(_FIELD_SMS), "Get a fare quote", team=False)
        assert [r["role"] for r in saved.rows] == ["user", "ai"]
        assert saved.rows[0]["content"] == "Get a fare quote"
        assert saved.rows[1]["model_used"] == proactive.MODEL_USED
        assert out.message == card_words.contact_first(None, "sms")
        assert [b["kind"] for b in out.buttons] == ["sms", "email"], "off hours: no waiting here, and no trip to change"
        assert card_buttons.CARD_BUTTONS_HEALTH["contact_first"] == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("conv, users", [
        (_reader({"contact_test": "choice"}), 0),                  # the choice side: the model asks the trip
        (_reader({"contact_test": "field"}), 0),                   # the field it opened was not said
        (_reader(_FIELD_SMS), 1),                                   # not the chat's first answer
        ({**_reader(_FIELD_SMS), "metadata": {**_FIELD_SMS, "proactive_trigger": "quote"}}, 0),  # not such a card
        ({**_reader(_FIELD_SMS), "metadata": {**_FIELD_SMS, "proactive_trigger": "note",
                                               "proactive_variant": "places"}}, 0),  # the note's bandit question
    ])
    async def test_otherwise_the_model_answers_get_a_fare_quote(self, conv, users):
        out, saved = await _yes(conv, "Get a fare quote", users=users)
        assert out is None and saved.rows == []
        assert card_buttons.CARD_BUTTONS_HEALTH["fell_through"] == 1

    @pytest.mark.asyncio
    async def test_after_the_contact_the_trip_is_asked(self):
        """The contact first, then the trip: the channel's answer asks where and when for a chat that knows no
        destination (card_words.channel_done, trip_known)."""
        saved = _Saved()
        req = ChatRequest(message="Text message: +1 210 555 0100", conversation_id=_CID,
                          visitor_id="visitor-0000000001", button="sms")
        lead = {"id": "L1", "intent_signals": {}, "notes": None}
        with patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=_reader(_FIELD_SMS))), \
             patch("app.services.conversation_service.add_message", new=saved), \
             patch("app.services.lead_service.update_lead_from_entities", new=AsyncMock(return_value=True)), \
             patch("app.services.lead_service.get_or_create_lead", new=AsyncMock(return_value=lead)), \
             patch("app.db.supabase.update_lead", new=AsyncMock(return_value=True)), \
             patch("app.services.routing.queue_for_person", new=AsyncMock(return_value=True)), \
             patch("app.services.tasks_service.withdraw_call_tasks", new=AsyncMock(return_value=0)), \
             patch("app.services.closing.is_within_team_hours", return_value=True):
            out = await card_buttons.answer(req, req.message, {})
        assert out.message.endswith("Where would you like to fly, and roughly when? Write it here.")


class _UserNotSaved(_Saved):
    """The client's tap is not kept: conversation_service.add_message answers None for it."""

    async def __call__(self, cid, role, content, model_used=None, cost=0.0, live_extra=None):
        if role == "user":
            return None
        return await super().__call__(cid, role, content, model_used, cost, live_extra)


@pytest.mark.asyncio
@pytest.mark.parametrize("conv, message", [(_conv(_FIELD_SMS), "Yes, send me fares"),
                                           (_reader(_FIELD_SMS), "Get a fare quote")])
async def test_a_tap_that_was_not_kept_is_never_answered_over_a_history_without_it(conv, message):
    """Rule 4 (CodeRabbit #273): the client's words are kept first; when they aren't, no answer is stored over a
    history that lacks them, and the model's turn saves them and answers instead."""
    saved = _UserNotSaved()
    req = ChatRequest(message=message, conversation_id=_CID, visitor_id="visitor-0000000001", button="yes")
    with patch("app.db.supabase.get_conversation_simple", new=AsyncMock(return_value=conv)), \
         patch("app.db.supabase.count_messages", new=AsyncMock(return_value=0)), \
         patch("app.services.conversation_service.add_message", new=saved), \
         patch("app.services.closing.is_within_team_hours", return_value=True):
        out = await card_buttons.answer(req, message, {})
    assert out is None and saved.rows == []
    assert card_buttons.CARD_BUTTONS_HEALTH["save_failures"] == 1
    assert card_buttons.CARD_BUTTONS_HEALTH["fell_through"] == 1
