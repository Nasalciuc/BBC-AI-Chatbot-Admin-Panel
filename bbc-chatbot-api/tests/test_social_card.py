"""The Meta ad's card (owner, 25 Nov 2026, lever 3 of "Mai multe chaturi și leaduri").

17-24 Nov: 1,297 paid visits from Facebook and Instagram ads to the /lp/ pages had a moment in 12 of them (0.9%): the
widget knew them as no paid visit and the reader needs 25 s with half a screen. The widget now asks with `social` at
the reading moment (bbc-widget triggers.ts); here, the server's half: the words, the one yes answered contact first,
the card's own channel button, the switch.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api.chat import ProactiveOfferRequest
from app.models.trip_context import TripContext
from app.services import proactive, proactive_i18n

_VISITOR = "visitor-0123456789"
_ASIA = TripContext(source="route_page", destination_name="Asia", cabin="business")


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr("app.flags._snapshot", {})
    monkeypatch.setenv("PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("PROACTIVE_PERCENT", "100")
    monkeypatch.delenv("PROACTIVE_TRIGGERS", raising=False)
    monkeypatch.delenv("CONTACT_FIELD_TEST_PERCENT", raising=False)


class TestTheMoment:
    def test_it_is_a_moment_the_widget_may_name_and_on_by_default(self):
        assert "social" in proactive.TRIGGERS and "social" in proactive.DEFAULT_TRIGGERS
        assert "social" in proactive.active_triggers()
        req = ProactiveOfferRequest(visitor_id=_VISITOR, trigger="social", trip=None)
        assert req.trigger == "social"

    def test_switched_off_it_is_off_alone_and_the_reader_stays(self, monkeypatch):
        monkeypatch.setenv("PROACTIVE_TRIGGERS", "kayak,ads,quote,reader")
        assert proactive.offer(_VISITOR, "social", None) == {
            "show": False, "arm": "trigger_off", "trigger": "social", "message": None, "quick_replies": [],
        }
        assert proactive.offer(_VISITOR, "reader", None)["show"] is True


class TestTheWords:
    def test_a_page_that_names_a_region_says_it_back_and_asks_one_yes(self):
        out = proactive.offer(_VISITOR, "social", _ASIA, site="bbc", device="phone", opens_field=True)
        assert out["show"] is True and out["arm"] == "proactive" and out["trigger"] == "social"
        assert out["message"] == (
            "Hi, I'm Alex from BuyBusinessClass. Want an agent to send you today's business class fares to Asia?"
        )
        assert out["quick_replies"] == ["Yes, send me fares", "I have a question", "Not now"]
        # The phone card leads with the place, and asks only the question under it (ADR A3).
        assert out["headline"] == "Asia · Business class"
        assert out["card_text"] == "Want an agent to send you today's business class fares to Asia?"

    def test_a_page_that_names_no_place_still_asks_one_yes(self):
        out = proactive.offer(_VISITOR, "social", None, site="bct", device="computer")
        assert out["message"] == (
            "Hi, I'm Alex from Business Class Tickets. Planning a business class trip? "
            "Want an agent to send you today's best fares?"
        )
        assert "headline" not in out

    def test_the_yes_is_answered_contact_first_with_the_email_field(self):
        out = proactive.offer(_VISITOR, "social", None, device="phone", opens_field=True)
        assert out["contact_test"] == "field"
        assert out["confirm"]["field"] == "email"
        assert "email" in out["confirm"]["message"].lower()

    @pytest.mark.parametrize("device, kind", [("phone", "sms"), ("computer", "email")])
    def test_the_card_carries_its_own_channel_button(self, device, kind):
        for trip in (None, _ASIA):
            out = proactive.offer(_VISITOR, "social", trip, device=device)
            assert out["channel"]["kind"] == kind

    @pytest.mark.parametrize("lang", ["es", "fr"])
    def test_spanish_and_french_have_their_words(self, lang):
        for trip in (None, _ASIA):
            out = proactive.offer(_VISITOR, "social", trip, lang=lang)
            assert out["lang"] == lang
            assert out["quick_replies"] == proactive_i18n.REPLIES[lang]["social"]
        assert "social_open" in proactive_i18n.SENTENCES[lang]

    def test_no_words_the_chatbot_must_not_write(self):
        for lang in ("en", "es", "fr"):
            for trip in (None, _ASIA):
                text, replies = proactive.compose("social", trip, "bbc", None, lang)
                assert "—" not in text, "no long dash in what the chatbot writes to clients"
                assert "$" not in text and not any(ch.isdigit() for ch in text), "never an amount"


class TestRule5:
    """The moments that are not the Meta card keep their words."""

    def test_the_ads_card_without_a_place_is_still_off_for_the_visit(self):
        out = proactive.offer(_VISITOR, "ads", None)
        assert out["show"] is False and out["arm"] == "trigger_off" and out["reason"] == "no_place"

    def test_the_reader_with_a_place_still_asks_when(self):
        text, _ = proactive.compose("reader", _ASIA, "bbc")
        assert "Tell me roughly when" in text

    def test_the_reader_has_no_channel_button_of_its_own(self):
        assert "channel" not in proactive.offer(_VISITOR, "reader", _ASIA, device="phone")

    def test_the_chat_remembers_which_moment_opened_it(self):
        meta = proactive.init_metadata("social", _ASIA)
        assert meta["proactive_trigger"] == "social"
        assert meta["proactive_trip"]["destination_name"] == "Asia"
