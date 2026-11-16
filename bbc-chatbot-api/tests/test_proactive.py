"""The chat that opens by itself, with the visitor's own search (no form).

Pins the backend half of the proactive chat:
- TripContext drops a bad field on its own, never the whole request;
- the card's words are composed on the server, from validated fields only;
- /chat/proactive/offer writes nothing and answers per arm (flag off → off,
  control → show=False, proactive → the words);
- /chat/init with `proactive` writes the opening as the bot's first message,
  labels the chat and seeds the lead — only on a chat that has no messages;
- the lead seed never sends a cabin the leads table refuses, and a failed
  seed is counted, logged without PII and never costs the visitor the chat;
- the prompt shows the page's search until the client changes it, and asks
  email first on a proactive chat;
- the metadata allowlist keeps a PostHog session id and nothing that is not one.

Dates are relative to today: the suite also runs 120 days ahead (--as-of).
"""

import logging
import os
import re
import sys
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.api.chat import (
    ChatInitRequest,
    ProactiveOfferRequest,
    chat_init,
    proactive_offer,
)
from app.models.chat import VisitorInfo
from app.models.trip_context import TripContext
from app.services import proactive

_CONV_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_VISITOR = "visitor-0123456789"


def _in(days: int) -> date:
    return date.today() + timedelta(days=days)


def _kayak_trip(**over) -> TripContext:
    """What the widget reads from a Kayak landing link (ATL → ICN, 4 in First)."""
    data = {
        "source": "kayak",
        "origin_code": "ATL",
        "destination_code": "ICN",
        "depart_date": _in(30).isoformat(),
        "return_date": _in(60).isoformat(),
        "trip_type": "round_trip",
        "cabin": "first",
        "adults": "4",
        "children": "0",
        "infants": "0",
    }
    data.update(over)
    return TripContext(**data)


def _day(d: date) -> str:
    return f"{('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')[d.month - 1]} {d.day}"


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    """Every test starts from merge state (feature off, 0%) with fresh counters."""
    flags._reset_for_tests()
    for k in ("PROACTIVE_ENABLED", "PROACTIVE_PERCENT", "PROACTIVE_TRIGGERS"):
        monkeypatch.delenv(k, raising=False)
    saved = dict(proactive.PROACTIVE_HEALTH)
    for k in ("offers", "shown", "opened", "open_failures", "seed_errors"):
        proactive.PROACTIVE_HEALTH[k] = 0
    proactive.PROACTIVE_HEALTH["last_seed_error_at"] = None
    yield
    proactive.PROACTIVE_HEALTH.clear()
    proactive.PROACTIVE_HEALTH.update(saved)
    flags._reset_for_tests()


def _switch_on(monkeypatch, percent: int = 100, triggers: str | None = None):
    monkeypatch.setenv("PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("PROACTIVE_PERCENT", str(percent))
    if triggers is not None:
        monkeypatch.setenv("PROACTIVE_TRIGGERS", triggers)


# ══════════════════════════════════════════════════════════════
# 1. TripContext: a bad field is dropped on its own
# ══════════════════════════════════════════════════════════════

class TestTripContext:
    def test_kayak_link_reads_whole(self):
        t = _kayak_trip(origin_code="atl", cabin="First Class")
        assert t.origin_code == "ATL" and t.destination_code == "ICN"
        assert t.depart_date == _in(30) and t.return_date == _in(60)
        assert t.cabin == "first" and t.trip_type == "round_trip"
        assert (t.adults, t.children, t.infants) == (4, 0, 0)
        assert t.passengers == 4 and t.has_route and not t.is_empty()

    @pytest.mark.parametrize("bad", ["ATLX", "A1L", "", "  ", 123, None, "ÅTL", "<b>"])
    def test_a_code_is_three_letters_or_nothing(self, bad):
        t = _kayak_trip(origin_code=bad)
        assert t.origin_code is None
        # The rest of the search survives the bad field.
        assert t.destination_code == "ICN" and t.depart_date == _in(30)

    @pytest.mark.parametrize("bad", ["<script>alert(1)</script>", "Paris1", "x", "a" * 41, "javascript:x", "{{name}}"])
    def test_a_city_name_with_markup_or_digits_is_dropped(self, bad):
        assert TripContext(destination_name=bad).destination_name is None

    def test_a_city_name_keeps_its_own_spelling(self):
        assert TripContext(destination_name="  São   Paulo ").destination_name == "São Paulo"
        assert TripContext(destination_name="St. John's").destination_name == "St. John's"

    @pytest.mark.parametrize("bad", ["2020-01-01", "not-a-date", "2026-13-45", 20270525, True])
    def test_an_impossible_date_is_dropped(self, bad):
        assert TripContext(depart_date=bad).depart_date is None

    def test_a_past_or_too_far_date_is_dropped(self):
        assert TripContext(depart_date=_in(-1).isoformat()).depart_date is None
        assert TripContext(depart_date=_in(401).isoformat()).depart_date is None
        assert TripContext(depart_date=_in(0).isoformat()).depart_date == _in(0)
        assert TripContext(depart_date=_in(400).isoformat()).depart_date == _in(400)

    def test_a_return_before_the_departure_is_dropped(self):
        t = _kayak_trip(return_date=_in(10).isoformat())
        assert t.depart_date == _in(30) and t.return_date is None

    def test_a_digit_int_cannot_read_drops_only_that_field(self):
        # CodeRabbit #255: "²".isdigit() is True and int("²") raises — that
        # failed the whole request instead of dropping the travellers.
        t = _kayak_trip(adults="²", children="³")
        assert t.adults is None and t.children is None
        assert t.destination_code == "ICN" and t.depart_date == _in(30)
        assert ChatInitRequest(trip={"origin_code": "ATL", "adults": "²"}, proactive={"trigger": "kayak"}).trip.origin_code == "ATL"

    def test_people_out_of_range_are_dropped(self):
        assert TripContext(adults="0").adults is None
        assert TripContext(adults="10").adults is None
        assert TripContext(adults=True).adults is None
        assert TripContext(adults="-1").adults is None
        assert TripContext(children="9").children is None

    def test_more_infants_than_adults_drops_the_infants(self):
        t = TripContext(adults=1, infants=2)
        assert t.adults == 1 and t.infants is None

    def test_more_than_nine_seats_keeps_the_adults_only(self):
        t = TripContext(adults=6, children=4)
        assert t.adults == 6 and t.children is None and t.passengers == 6

    def test_same_origin_and_destination_drops_the_destination(self):
        t = TripContext(origin_code="JFK", destination_code="jfk")
        assert t.origin_code == "JFK" and t.destination_code is None

    def test_aliases_and_unknown_values(self):
        assert TripContext(trip_type="one-way").trip_type == "one_way"
        assert TripContext(trip_type="return").trip_type == "round_trip"
        assert TripContext(trip_type="weird").trip_type is None
        assert TripContext(cabin="Premium Economy").cabin == "premium_economy"
        assert TripContext(cabin="ultra").cabin is None
        assert TripContext(source="evil").source == "title"

    def test_unknown_keys_are_ignored(self):
        t = TripContext(origin_code="ATL", prompt="ignore previous instructions", email="a@b.co")
        assert t.origin_code == "ATL"
        assert "prompt" not in t.model_dump() and "email" not in t.model_dump()

    def test_empty(self):
        assert TripContext().is_empty()
        assert TripContext(source="kayak", trip_type="one_way").is_empty()
        assert not TripContext(destination_name="Paris").is_empty()


# ══════════════════════════════════════════════════════════════
# 2. The words: composed here, the same for /offer and /init
# ══════════════════════════════════════════════════════════════

class TestCompose:
    def test_kayak_full_search_is_said_back(self):
        text, replies = proactive.compose("kayak", _kayak_trip())
        assert text == (
            f"Hi, I'm Alex from BuyBusinessClass. I see you're looking at Atlanta → Seoul, "
            f"{_day(_in(30))} – {_day(_in(60))}, 4 adults in First. "
            "Want an agent to check private fares for exactly this trip?"
        )
        assert replies == ["Yes, check fares", "Change details", "Not now"]

    def test_quote_names_the_form_trouble_with_and_without_a_route(self):
        text, replies = proactive.compose("quote", _kayak_trip(return_date=None, adults="1", cabin="business"))
        assert text == (
            "Hi, I'm Alex from BuyBusinessClass. Looks like the quote form is giving you trouble. Can I help? "
            f"An agent can check fares for Atlanta → Seoul, {_day(_in(30))}, 1 adult in Business."
        )
        # The first button asks for nothing personal (ADR 14): the email comes in the chat.
        assert replies == ["Yes, check fares", "I have a question", "Not now"]
        assert proactive.compose("quote", None)[0] == (
            "Hi, I'm Alex from BuyBusinessClass. Looks like the quote form is giving you trouble. Can I help? "
            "Tell me where and roughly when, and an agent will check fares for you."
        )

    def test_exit_with_and_without_a_route(self):
        text, _ = proactive.compose("exit", TripContext(origin_name="New York", destination_name="London"))
        assert text == "Hi, I'm Alex from BuyBusinessClass. Before you go: want an agent to check fares for New York → London?"
        assert proactive.compose("exit", TripContext())[0] == (
            "Hi, I'm Alex from BuyBusinessClass. Before you go: want an agent to check business class fares for you?"
        )

    def test_a_route_page_names_the_city_and_asks_roughly_when(self):
        text, replies = proactive.compose("reader", TripContext(source="route_page", destination_name="Paris"))
        assert text == (
            "Hi, I'm Alex from BuyBusinessClass. Planning a trip to Paris? "
            "Tell me roughly when, and an agent will check business class fares for you."
        )
        assert replies == ["Check fares", "I have a question", "Not now"]

    def test_nothing_known_is_generic(self):
        assert proactive.compose("reader", None)[0] == (
            "Hi, I'm Alex from BuyBusinessClass. Planning a business class trip? "
            "Tell me where and roughly when, and an agent will check fares for you."
        )

    def test_the_brand_follows_the_site(self):
        assert proactive.compose("reader", None, "bct")[0].startswith("Hi, I'm Alex from Business Class Tickets.")
        assert proactive.compose("kayak", _kayak_trip(), "bbc")[0].startswith("Hi, I'm Alex from BuyBusinessClass.")
        # Unknown or missing: BBC, the way the prompt's brand defaults.
        for site in (None, "", "evil.example", "BCT"):
            assert proactive.compose("exit", None, site)[0].startswith("Hi, I'm Alex from BuyBusinessClass.")

    @pytest.mark.parametrize("trigger", ["kayak", "quote", "reader", "exit"])
    @pytest.mark.parametrize("trip_kind", ["full", "dest", "none"])
    @pytest.mark.parametrize("site", ["bbc", "bct"])
    def test_no_opening_promises_a_time_or_a_price_or_asks_what_the_page_knows(self, trigger, trip_kind, site):
        """ADR 11 and 15 (16 Nov debate): a third of the /flight chats stopped at
        "Where are you flying from?", and a third of the chats come at night."""
        trip = {"full": _kayak_trip(), "dest": TripContext(destination_name="Paris"), "none": None}[trip_kind]
        text, _ = proactive.compose(trigger, trip, site)
        lowered = text.lower()
        assert not re.search(r"\d+\s*(min|minute|hour|hr)", lowered), text
        assert "$" not in text and "usd" not in lowered and "within" not in lowered, text
        assert "where are you flying" not in lowered, text
        if trip_kind == "full":
            # The page gave the route and the dates: nothing asks for them.
            assert "tell me where" not in lowered and "roughly when" not in lowered, text

    def test_a_difficulty_is_named_only_where_there_was_one(self):
        """ADR 13: the Kayak visitor hesitates, he has no problem; only the quote
        form that did not send is a difficulty."""
        for trigger in ("kayak", "reader", "exit"):
            for trip in (_kayak_trip(), None):
                assert "trouble" not in proactive.compose(trigger, trip)[0]
        assert "trouble" in proactive.compose("quote", None)[0]

    def test_children_and_infants_are_counted(self):
        text, _ = proactive.compose("kayak", _kayak_trip(adults="2", children="1", infants="1"))
        assert "2 adults, 1 child, 1 infant in First" in text

    def test_an_unknown_code_is_shown_as_the_code(self):
        text, _ = proactive.compose("kayak", _kayak_trip(destination_code="QQQ"))
        assert "Atlanta → QQQ" in text

    def test_markup_in_a_name_never_reaches_the_words(self):
        text, _ = proactive.compose("reader", TripContext(destination_name="<img src=x onerror=alert(1)>"))
        assert "<" not in text and "onerror" not in text


# ══════════════════════════════════════════════════════════════
# 3. /chat/proactive/offer: writes nothing, answers per arm
# ══════════════════════════════════════════════════════════════

class TestOffer:
    def test_off_at_merge(self):
        out = proactive.offer(_VISITOR, "kayak", _kayak_trip())
        assert out == {"show": False, "arm": "off", "trigger": "kayak", "message": None, "quick_replies": []}
        assert proactive.PROACTIVE_HEALTH["offers"] == 1 and proactive.PROACTIVE_HEALTH["shown"] == 0

    def test_proactive_arm_gets_the_words(self, monkeypatch):
        _switch_on(monkeypatch, percent=100)
        out = proactive.offer(_VISITOR, "kayak", _kayak_trip())
        assert out["show"] is True and out["arm"] == "proactive"
        assert out["message"] == proactive.compose("kayak", _kayak_trip())[0]
        assert out["quick_replies"] == ["Yes, check fares", "Change details", "Not now"]
        assert proactive.PROACTIVE_HEALTH["shown"] == 1

    def test_control_arm_is_told_no(self, monkeypatch):
        _switch_on(monkeypatch, percent=0)
        out = proactive.offer(_VISITOR, "kayak", _kayak_trip())
        assert out["show"] is False and out["arm"] == "control" and out["message"] is None

    def test_arm_is_stable_per_visitor(self, monkeypatch):
        _switch_on(monkeypatch, percent=50)
        arms = {proactive.offer(_VISITOR, "reader", None)["arm"] for _ in range(5)}
        assert len(arms) == 1

    def test_a_trigger_switched_off_is_off(self, monkeypatch):
        _switch_on(monkeypatch, percent=100, triggers="kayak,quote")
        assert proactive.offer(_VISITOR, "exit", None)["arm"] == "off"
        assert proactive.offer(_VISITOR, "quote", None)["show"] is True

    def test_offer_never_touches_the_database(self, monkeypatch):
        _switch_on(monkeypatch, percent=100)
        with patch("app.db.supabase.get_client", side_effect=AssertionError("offer wrote")):
            assert proactive.offer(_VISITOR, "kayak", _kayak_trip())["show"] is True

    @pytest.mark.asyncio
    async def test_endpoint_answers_and_drops_a_bad_trip(self, monkeypatch):
        _switch_on(monkeypatch, percent=100)
        req = ProactiveOfferRequest(visitor_id=_VISITOR, trigger="reader", trip="not an object")
        out = await proactive_offer(req, None)
        assert out["show"] is True
        assert out["message"].startswith("Hi, I'm Alex from BuyBusinessClass. Planning a business class trip?")

    @pytest.mark.asyncio
    async def test_the_card_names_the_brand_of_the_site(self, monkeypatch):
        _switch_on(monkeypatch, percent=100)
        req = ProactiveOfferRequest(visitor_id=_VISITOR, trigger="exit", site="bct")
        assert req.site == "bct"
        out = await proactive_offer(req, None)
        assert out["message"].startswith("Hi, I'm Alex from Business Class Tickets.")

    @pytest.mark.parametrize("site", ["evil.example", "BBC", 7, ["bct"], {"x": 1}, ""])
    def test_an_unknown_site_is_dropped_never_a_422(self, site):
        req = ProactiveOfferRequest(visitor_id=_VISITOR, trigger="exit", site=site)
        assert req.site is None


# ══════════════════════════════════════════════════════════════
# 4. The opening: written only on a chat that has no messages
# ══════════════════════════════════════════════════════════════

def _open_patches(msg_count=0, stored=None):
    return (
        patch("app.db.supabase.count_messages", new=AsyncMock(return_value=msg_count)),
        patch(
            "app.services.conversation_service.add_message",
            new=AsyncMock(return_value=stored if stored is not None else {
                "id": "m1", "content": "stored words", "created_at": "2026-11-15T03:00:00.123456+00:00",
            }),
        ),
        patch("app.db.supabase._merge_conv_metadata", new=AsyncMock()),
        patch("app.services.proactive.seed_lead", new=AsyncMock()),
    )


class TestOpenConversation:
    @pytest.mark.asyncio
    async def test_empty_chat_gets_opening_label_and_seed(self):
        trip = _kayak_trip()
        p_count, p_add, p_meta, p_seed = _open_patches()
        with p_count, p_add as add, p_meta as meta, p_seed as seed:
            out = await proactive.open_conversation(_CONV_ID, "kayak", trip)

        # What the output guard stored, not the raw text, and when: the widget
        # polls after it so the opening is not drawn twice.
        assert out == {"content": "stored words", "created_at": "2026-11-15T03:00:00.123456+00:00"}
        add.assert_awaited_once()
        args, kwargs = add.await_args
        assert args[:3] == (_CONV_ID, "ai", proactive.compose("kayak", trip)[0])
        assert kwargs == {"model_used": "proactive_template", "cost": 0.0}
        meta.assert_awaited_once()
        written = meta.await_args.args[1]
        assert written["proactive_trigger"] == "kayak" and written["proactive_at"]
        assert written["proactive_trip"]["destination_code"] == "ICN"
        seed.assert_awaited_once_with(_CONV_ID, trip)
        assert proactive.PROACTIVE_HEALTH["opened"] == 1

    @pytest.mark.asyncio
    async def test_the_opening_names_the_brand_of_the_site(self):
        p_count, p_add, p_meta, p_seed = _open_patches()
        with p_count, p_add as add, p_meta, p_seed:
            await proactive.open_conversation(_CONV_ID, "reader", None, "bct")
        assert add.await_args.args[2] == proactive.compose("reader", None, "bct")[0]
        assert add.await_args.args[2].startswith("Hi, I'm Alex from Business Class Tickets.")

    @pytest.mark.asyncio
    async def test_a_chat_with_messages_is_left_alone(self):
        p_count, p_add, p_meta, p_seed = _open_patches(msg_count=3)
        with p_count, p_add as add, p_meta as meta, p_seed as seed:
            assert await proactive.open_conversation(_CONV_ID, "kayak", _kayak_trip()) is None
        add.assert_not_awaited()
        meta.assert_not_awaited()  # no proactive label on a chat the card did not open
        seed.assert_not_awaited()
        assert proactive.PROACTIVE_HEALTH["opened"] == 0

    @pytest.mark.asyncio
    async def test_an_opening_not_saved_is_a_plain_chat(self, caplog):
        # CodeRabbit #255: db.add_message returns None when the insert fails.
        # Nothing may then say the opening was shown: no text back, no label,
        # no page trip, no "opened".
        p_count, p_add, p_meta, p_seed = _open_patches()
        with p_count, patch("app.services.conversation_service.add_message", new=AsyncMock(return_value=None)), \
             p_meta as meta, p_seed as seed, caplog.at_level(logging.ERROR):
            assert await proactive.open_conversation(_CONV_ID, "kayak", _kayak_trip()) is None
        meta.assert_not_awaited()
        seed.assert_not_awaited()
        assert proactive.PROACTIVE_HEALTH["opened"] == 0
        assert proactive.PROACTIVE_HEALTH["open_failures"] == 1
        assert "proactive opening not saved" in caplog.text

    @pytest.mark.asyncio
    async def test_no_trip_no_seed(self):
        p_count, p_add, p_meta, p_seed = _open_patches(stored={})
        with p_count, p_add, p_meta as meta, p_seed as seed:
            out = await proactive.open_conversation(_CONV_ID, "reader", None)
        # Nothing came back from the write: the composed words, no cursor.
        assert out == {"content": proactive.compose("reader", None)[0], "created_at": None}
        assert "proactive_trip" not in meta.await_args.args[1]
        seed.assert_not_awaited()


# ══════════════════════════════════════════════════════════════
# 5. The lead seed
# ══════════════════════════════════════════════════════════════

class _Table:
    """Just enough of the supabase query chain for _mark_page_trip."""

    def __init__(self, rows):
        self.rows = rows
        self.updates: list[dict] = []

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def update(self, payload):
        self.updates.append(payload)
        return self

    def execute(self):
        return MagicMock(data=self.rows)


async def _run_now(fn):
    return fn()


class TestSeedLead:
    @pytest.mark.asyncio
    async def test_the_page_search_lands_on_the_lead(self):
        with patch("app.services.lead_service.ensure_lead", new=AsyncMock(return_value={"id": "L1", "trip_notes": None})) as ensure, \
             patch("app.services.lead_service.update_lead_from_entities", new=AsyncMock()) as upd, \
             patch("app.services.proactive._mark_page_trip", new=AsyncMock()) as mark:
            await proactive.seed_lead(_CONV_ID, _kayak_trip())
        ensure.assert_awaited_once_with(_CONV_ID)
        entities = upd.await_args.args[1]
        assert entities == {
            "origin": "ATL",
            "destination": "ICN",
            "departure_date": _in(30).isoformat(),
            "return_date": _in(60).isoformat(),
            "passengers": 4,
            "_children_count": 0,
            "_infant_count": 0,
            "cabin_class": "first",
        }
        # The page's trip type is not said by the client: never sent, so the
        # writer cannot mark it trip_type_source='client'.
        assert "trip_type" not in entities
        mark.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_premium_economy_is_not_sent_to_the_lead(self):
        """leads_cabin_class_check takes business/first/economy/mixed: a
        premium_economy cabin would refuse the whole update, route included."""
        with patch("app.services.lead_service.ensure_lead", new=AsyncMock(return_value={"id": "L1", "trip_notes": {}})), \
             patch("app.services.lead_service.update_lead_from_entities", new=AsyncMock()) as upd, \
             patch("app.services.proactive._mark_page_trip", new=AsyncMock()):
            await proactive.seed_lead(_CONV_ID, _kayak_trip(cabin="premium_economy"))
        entities = upd.await_args.args[1]
        assert "cabin_class" not in entities and entities["destination"] == "ICN"

    @pytest.mark.asyncio
    async def test_without_trip_notes_nothing_is_written_as_the_clients(self):
        # CodeRabbit #255: before migration 040 nothing could mark the route
        # and dates as the page's; written anyway, the prompt and the CRM
        # would take them as said by the client.
        with patch("app.services.lead_service.ensure_lead", new=AsyncMock(return_value={"id": "L1"})), \
             patch("app.services.lead_service.update_lead_from_entities", new=AsyncMock()) as upd, \
             patch("app.services.proactive._mark_page_trip", new=AsyncMock()) as mark:
            await proactive.seed_lead(_CONV_ID, _kayak_trip())
        upd.assert_not_awaited()
        mark.assert_not_awaited()
        assert proactive.PROACTIVE_HEALTH["seed_errors"] == 0

    @pytest.mark.asyncio
    async def test_no_lead_row_is_a_counted_failure(self):
        with patch("app.services.lead_service.ensure_lead", new=AsyncMock(return_value=None)), \
             patch("app.services.lead_service.update_lead_from_entities", new=AsyncMock()) as upd:
            await proactive.seed_lead(_CONV_ID, _kayak_trip())
        upd.assert_not_awaited()
        assert proactive.PROACTIVE_HEALTH["seed_errors"] == 1

    @pytest.mark.asyncio
    async def test_a_failed_seed_is_counted_and_logged_without_pii(self, caplog):
        boom = RuntimeError("duplicate key for jane.doe@example.com +15551234567")
        with patch("app.services.lead_service.ensure_lead", new=AsyncMock(side_effect=boom)), \
             caplog.at_level(logging.INFO):
            await proactive.seed_lead(_CONV_ID, _kayak_trip())  # never raises
        assert proactive.PROACTIVE_HEALTH["seed_errors"] == 1
        assert proactive.PROACTIVE_HEALTH["last_seed_error_at"]
        assert "RuntimeError" in caplog.text
        assert "jane.doe" not in caplog.text and "5551234567" not in caplog.text

    @pytest.mark.asyncio
    async def test_page_trip_is_written_unconfirmed_and_keeps_other_notes(self):
        table = _Table([{"id": "L1", "trip_notes": {"legs": ["kept"], "trip_type_source": "client"}}])
        client = MagicMock(table=MagicMock(return_value=table))
        with patch("app.db.supabase.get_client", return_value=client), \
             patch("app.db.supabase._run_sync", new=_run_now):
            await proactive._mark_page_trip(_CONV_ID, _kayak_trip())
        notes = table.updates[0]["trip_notes"]
        assert notes["legs"] == ["kept"]
        assert notes["trip_type_source"] == "client"  # never downgraded
        assert notes["page_trip"]["confirmed"] is False
        assert notes["page_trip"]["destination_code"] == "ICN"
        assert notes["page_trip"]["source"] == "kayak"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("case", ["all_landed", "refused", "page_said_business", "premium_economy"])
    async def test_the_page_trip_records_what_the_seed_wrote(self, case):
        row, trip, landed = {
            # The whole seed landed.
            "all_landed": (
                {"origin_code": "ATL", "destination_code": "ICN", "departure_date": _in(30).isoformat(),
                 "return_date": _in(60).isoformat(), "passengers": 4, "cabin_class": "first"},
                _kayak_trip(), _ALL_SEEDED,
            ),
            # The update was refused (logged by the writer, not raised): the lead is as new.
            "refused": ({"cabin_class": "business"}, _kayak_trip(), []),
            # The page said Business: the lead says the same, written or not.
            "page_said_business": (
                {"cabin_class": "business"}, TripContext(destination_name="Paris", cabin="business"), ["cabin_class"],
            ),
            # Premium Economy is never written on the lead.
            "premium_economy": (
                {"destination_code": "ICN", "cabin_class": "business"},
                TripContext(destination_code="ICN", cabin="premium_economy"), ["destination_code"],
            ),
        }[case]
        table = _Table([{"id": "L1", "trip_notes": {}, **row}])
        client = MagicMock(table=MagicMock(return_value=table))
        with patch("app.db.supabase.get_client", return_value=client), \
             patch("app.db.supabase._run_sync", new=_run_now):
            await proactive._mark_page_trip(_CONV_ID, trip)
        assert table.updates[0]["trip_notes"]["page_trip"]["landed"] == landed

    @pytest.mark.asyncio
    async def test_no_trip_notes_column_no_write(self):
        table = _Table([{"id": "L1"}])  # migration 040 not applied
        client = MagicMock(table=MagicMock(return_value=table))
        with patch("app.db.supabase.get_client", return_value=client), \
             patch("app.db.supabase._run_sync", new=_run_now):
            await proactive._mark_page_trip(_CONV_ID, _kayak_trip())
        assert table.updates == []


# ══════════════════════════════════════════════════════════════
# 6. The prompt: the page's search until the client changes it
# ══════════════════════════════════════════════════════════════

_ALL_SEEDED = ["origin_code", "destination_code", "departure_date", "return_date", "passengers", "cabin_class"]


def _seeded_lead(landed=_ALL_SEEDED, **over) -> dict:
    """A lead the Kayak seed reached. `landed` is what the seed recorded it
    wrote (all of it by default); None: a page trip without the record."""
    trip = _kayak_trip()
    page = {**trip.model_dump(mode="json", exclude_none=True), "confirmed": False}
    if landed is not None:
        page["landed"] = list(landed)
    lead = {
        "id": "L1",
        "score": 20,
        "origin_code": "ATL",
        "destination_code": "ICN",
        "departure_date": _in(30).isoformat(),
        "return_date": _in(60).isoformat(),
        "passengers": 4,
        "cabin_class": "first",
        "trip_notes": {"page_trip": page},
    }
    lead.update(over)
    return lead


class TestPageTripLine:
    def test_shown_while_the_lead_holds_the_page_search(self):
        line = proactive.page_trip_line(_seeded_lead())
        assert line.startswith("From the page (the client's own search, already shown in your opening message): ")
        assert f"Atlanta → Seoul, {_day(_in(30))} – {_day(_in(60))}, 4 adults in First." in line
        assert "the client's words win" in line

    @pytest.mark.parametrize("landed", [[], None])
    @pytest.mark.parametrize("cabin", ["business", None])
    def test_shown_when_the_seed_never_landed(self, cabin, landed):
        # One update writes every seeded field, so a seed that failed left none of them (and recorded
        # none). The cabin stays at the column's default ("business", on every new lead), which says
        # nothing.
        lead = _seeded_lead(landed=landed, origin_code=None, destination_code=None, departure_date=None,
                            return_date=None, passengers=None, cabin_class=cabin)
        assert proactive.page_trip_line(lead) is not None

    def test_a_cabin_the_seed_wrote_then_the_client_set_to_business(self):
        # CodeRabbit #255 (round 2): a route page gave a city (never on the lead) and First (written).
        # The client then said Business, the column's default: the record says the seed wrote the
        # cabin, so Business is the client's and the page's "First" must go.
        page = {"source": "route_page", "destination_name": "Paris", "cabin": "first", "confirmed": False,
                "landed": ["cabin_class"]}
        lead = lambda cabin: {"id": "L1", "cabin_class": cabin, "trip_notes": {"page_trip": dict(page)}}
        assert proactive.page_trip_line(lead("business")) is None
        assert proactive.page_trip_line(lead("first")) is not None

    def test_a_seed_that_never_landed_still_gives_way_to_the_clients_route(self):
        lead = _seeded_lead(landed=[], origin_code=None, destination_code="NRT", departure_date=None,
                            return_date=None, passengers=None, cabin_class="business")
        assert proactive.page_trip_line(lead) is None

    def test_a_route_page_with_only_a_cabin(self):
        # A route page's title gives a city (never written on the lead) and a cabin (written).
        page = {"source": "route_page", "destination_name": "Paris", "cabin": "first", "confirmed": False}
        lead = lambda cabin: {"id": "L1", "cabin_class": cabin, "trip_notes": {"page_trip": dict(page)}}
        assert proactive.page_trip_line(lead("first")) is not None      # the seed wrote First
        assert proactive.page_trip_line(lead("business")) is not None   # the default: the seed never landed
        assert proactive.page_trip_line(lead("economy")) is None        # the client said Economy
        page["cabin"] = "business"
        assert proactive.page_trip_line(lead("business")) is not None
        assert proactive.page_trip_line(lead("economy")) is None

    def test_gone_once_the_client_names_another_destination(self):
        assert proactive.page_trip_line(_seeded_lead(destination_code="NRT")) is None

    def test_gone_once_the_client_moves_the_dates(self):
        assert proactive.page_trip_line(_seeded_lead(departure_date=_in(31).isoformat())) is None

    def test_gone_once_the_client_drops_the_return(self):
        assert proactive.page_trip_line(_seeded_lead(return_date=None)) is None

    def test_gone_once_the_client_changes_the_travellers_or_the_cabin(self):
        # CodeRabbit #255: the line shows "4 adults in First"; a client who
        # said one traveller or Business must not be told it again.
        assert proactive.page_trip_line(_seeded_lead(passengers=1)) is None
        assert proactive.page_trip_line(_seeded_lead(cabin_class="business")) is None

    def test_premium_economy_is_not_on_the_lead_and_is_no_change(self):
        lead = _seeded_lead(cabin_class=None)
        lead["trip_notes"]["page_trip"]["cabin"] = "premium_economy"
        assert "in Premium Economy" in proactive.page_trip_line(lead)

    def test_a_route_page_named_in_words_gives_way_to_the_clients_route(self):
        # CodeRabbit #255: a page trip of "Paris" (a title, no code) stayed in
        # the prompt after the client asked for Tokyo.
        page = {"source": "route_page", "destination_name": "Paris", "confirmed": False}
        lead = {"id": "L1", "trip_notes": {"page_trip": page}}
        assert proactive.page_trip_line(lead).startswith("From the page")
        lead_tokyo = {"id": "L1", "destination_code": "NRT", "trip_notes": {"page_trip": page}}
        assert proactive.page_trip_line(lead_tokyo) is None

    def test_gone_once_confirmed(self):
        lead = _seeded_lead()
        lead["trip_notes"]["page_trip"]["confirmed"] = True
        assert proactive.page_trip_line(lead) is None

    @pytest.mark.parametrize("notes", [None, "x", {}, {"page_trip": "x"}, {"page_trip": {"origin_code": 5}}])
    def test_nothing_readable_no_line(self, notes):
        assert proactive.page_trip_line({"trip_notes": notes}) is None

    def test_no_lead_no_line(self):
        assert proactive.page_trip_line(None) is None


class TestCrmNote:
    """The abandoned-lead job may send a proactive lead before the client
    confirmed the route: the consultant reads where it came from."""

    def test_note_only_while_the_lead_holds_the_page_search(self):
        assert proactive.page_trip_note(_seeded_lead()) == (
            "Route and dates from the Kayak link the client came from, not confirmed in chat"
        )
        assert proactive.page_trip_note(_seeded_lead(destination_code="NRT")) is None
        absent = _seeded_lead(landed=[], origin_code=None, destination_code=None, departure_date=None,
                              return_date=None, passengers=None, cabin_class="business")
        assert proactive.page_trip_note(absent) is None  # nothing of the page on the lead to warn about
        confirmed = _seeded_lead()
        confirmed["trip_notes"]["page_trip"]["confirmed"] = True
        assert proactive.page_trip_note(confirmed) is None
        assert proactive.page_trip_note({"id": "L1"}) is None

    def test_the_note_stays_when_only_the_travellers_or_the_cabin_change(self):
        # CodeRabbit #255 (round 2): the note is about the route and the dates. A client who changed
        # the travellers or the cabin never confirmed those; the prompt's line goes (it repeats the
        # travellers and the cabin), the consultant's warning stays.
        for lead in (_seeded_lead(passengers=1), _seeded_lead(cabin_class="business")):
            assert proactive.page_trip_note(lead) == (
                "Route and dates from the Kayak link the client came from, not confirmed in chat"
            )
            assert proactive.page_trip_line(lead) is None
        # A page trip without the record: the same.
        assert proactive.page_trip_note(_seeded_lead(landed=None, passengers=1)) is not None

    def test_no_note_for_the_route_the_client_gave_after_a_failed_seed(self):
        # The seed wrote nothing; the route on the lead is the client's, even where it matches the page.
        assert proactive.page_trip_note(_seeded_lead(landed=[])) is None

    def test_a_search_on_the_site_names_the_page(self):
        lead = _seeded_lead()
        lead["trip_notes"]["page_trip"]["source"] = "search"
        assert proactive.page_trip_note(lead).startswith("Route and dates from the page ")

    @pytest.mark.asyncio
    async def test_the_abandoned_push_carries_the_note(self):
        from app.services import crm

        conv = {
            "id": _CONV_ID,
            "visitor_name": "Test Client",
            "visitor_email": "client@example.com",
            "visitor_phone": "+12025550147",
            "metadata": {"utm_source": "kayak", "proactive_trigger": "kayak"},
            "visitor_id": _VISITOR,
            "status": "active",
            "mode": "ai",
            "created_at": "2026-11-15T03:00:00+00:00",
        }
        resp = MagicMock(status_code=200, text="")
        resp.json.return_value = {"data": {"id": "R-1"}}
        client = MagicMock(post=AsyncMock(return_value=resp))
        with patch.object(crm, "_get_crm_client", return_value=client), \
             patch.object(crm.settings, "crm_api_url", "https://crm.test"):
            res = await crm.submit_abandoned_to_crm(conv, _seeded_lead(trip_type="round_trip"))
        assert res.success
        assert "not confirmed in chat" in client.post.call_args.kwargs["json"]["chat_context"]


class TestPrompt:
    @pytest.mark.parametrize("site, brand", [("bbc", "Buy Business Class"), ("bct", "Business Class Tickets")])
    def test_the_assistant_is_alex_and_says_it_is_an_ai(self, site, brand):
        """ADR 12: the name the widget's header shows, on every chat, next to
        the rule that it never passes for a human."""
        from app.ai.prompts import build_conversational_prompt

        static, dynamic = build_conversational_prompt(
            tunnel="sales", visitor=VisitorInfo(), lead=None, entities={"site": site},
        )
        prompt = static + dynamic
        assert f"Your name is Alex, the AI travel agent of {brand}" in prompt
        assert "You ARE an AI travel agent, never a person." in prompt
        assert "NEVER imply you are a human" in prompt

    def test_proactive_chat_shows_the_page_and_asks_email_first(self):
        from app.ai.prompts import build_conversational_prompt

        _static, dynamic = build_conversational_prompt(
            tunnel="sales",
            visitor=VisitorInfo(),
            lead=_seeded_lead(),
            entities={"_metadata": {"proactive_trigger": "kayak"}},
        )
        assert "From the page (the client's own search" in dynamic
        assert "Still needed: email, name, phone number" in dynamic

    def test_label_alone_still_asks_email_first(self):
        """The client changed the search (no page line): still the chat the
        card opened, so the lightest contact is asked first."""
        from app.ai.prompts import build_conversational_prompt

        _static, dynamic = build_conversational_prompt(
            tunnel="sales",
            visitor=VisitorInfo(),
            lead=_seeded_lead(destination_code="NRT"),
            entities={"_metadata": {"proactive_trigger": "kayak"}},
        )
        assert "From the page" not in dynamic
        assert "Still needed: email, name, phone number" in dynamic

    def test_plain_chat_keeps_todays_order(self):
        from app.ai.prompts import build_conversational_prompt

        lead = _seeded_lead()
        lead.pop("trip_notes")
        _static, dynamic = build_conversational_prompt(tunnel="sales", visitor=VisitorInfo(), lead=lead)
        assert "From the page" not in dynamic
        assert "Still needed: name, email, phone number" in dynamic


class TestMissingFieldsOrder:
    def _lead(self):
        return {
            "origin_code": "ATL", "destination_code": "ICN", "departure_date": _in(30).isoformat(),
            "return_date": _in(60).isoformat(), "passengers": 2,
        }

    def test_default_order(self):
        from app.models.lead import get_missing_fields
        assert get_missing_fields(self._lead(), {}) == ["name", "email", "phone number"]

    def test_email_first(self):
        from app.models.lead import get_missing_fields
        assert get_missing_fields(self._lead(), {"email_first": True}) == ["email", "name", "phone number"]

    def test_email_first_skips_what_is_known(self):
        from app.models.lead import get_missing_fields
        got = get_missing_fields(self._lead(), {"email_first": True, "visitor_email": "x"})
        assert got == ["name", "phone number"]

    def test_email_comes_right_after_the_route(self):
        """16 Nov contact analysis: a client who leaves after giving an email
        can still be helped; the dates and travellers can follow it."""
        from app.models.lead import get_missing_fields
        lead = {"origin_code": "ATL", "destination_code": "ICN"}
        assert get_missing_fields(lead, {"email_first": True}) == [
            "email", "departure date", "number of travelers (adults, children, infants)",
            "name", "phone number",
        ]

    def test_without_a_route_the_route_comes_first_then_the_email(self):
        from app.models.lead import get_missing_fields
        assert get_missing_fields({"destination_code": "LHR"}, {"email_first": True})[:3] == [
            "route (origin and destination)", "email", "departure date",
        ]

    def test_the_email_is_asked_once(self):
        from app.models.lead import get_missing_fields
        got = get_missing_fields({"origin_code": "ATL", "destination_code": "ICN"}, {"email_first": True})
        assert got.count("email") == 1

    def test_a_form_chat_and_the_crm_keep_their_order(self):
        from app.models.lead import get_missing_fields
        lead = {"origin_code": "ATL", "destination_code": "ICN"}
        assert get_missing_fields(lead, {})[-3:] == ["name", "email", "phone number"]
        crm = get_missing_fields(lead, {"email_first": True}, for_crm=True)
        assert crm == ["number of travelers (how many passengers?)", "departure date", "email", "name", "phone number"]


# ══════════════════════════════════════════════════════════════
# 7. /chat/init with `proactive`
# ══════════════════════════════════════════════════════════════

def _request_mock() -> MagicMock:
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="127.0.0.1")
    return req


def _init_patches(open_side_effect=None):
    return (
        patch("app.api.chat.is_blocked", new=AsyncMock(return_value=False)),
        patch(
            "app.services.conversation_service.get_or_create_conversation",
            new=AsyncMock(return_value={"id": _CONV_ID}),
        ),
        patch(
            "app.services.proactive.open_conversation",
            new=AsyncMock(
                return_value={"content": "the opening", "created_at": "2026-11-15T03:00:00+00:00"},
                side_effect=open_side_effect,
            ),
        ),
    )


class TestChatInit:
    @pytest.mark.asyncio
    async def test_the_card_answer_opens_the_chat_with_the_opening(self, monkeypatch):
        _switch_on(monkeypatch)
        trip = _kayak_trip()
        payload = ChatInitRequest(visitor_id=_VISITOR, trip=trip.model_dump(mode="json"), proactive={"trigger": "kayak"})
        p_block, p_conv, p_open = _init_patches()
        with p_block, p_conv as conv, p_open as opener:
            res = await chat_init(payload, _request_mock(), None)
        assert res.conversation_id == _CONV_ID and res.opening == "the opening"
        assert res.opening_at == "2026-11-15T03:00:00+00:00"
        args = opener.await_args.args
        assert args[0] == _CONV_ID and args[1] == "kayak" and args[2].destination_code == "ICN"
        assert args[3] is None  # no site in the metadata: the opening speaks for BBC
        # The label is written with the opening, not at creation: a reused
        # chat must not be labelled proactive (first-touch metadata merge).
        created_meta = conv.await_args.kwargs["metadata"] or {}
        assert not any(k.startswith("proactive") for k in created_meta)

    @pytest.mark.parametrize("sent, expected", [("bct", "bct"), ("bbc", "bbc"), ("evil.example", None)])
    @pytest.mark.asyncio
    async def test_the_opening_gets_the_validated_site(self, monkeypatch, sent, expected):
        """The same brand the card named at /offer, as the metadata allowlist
        validated it: an unknown site never reaches the words."""
        _switch_on(monkeypatch)
        payload = ChatInitRequest(visitor_id=_VISITOR, metadata={"site": sent}, proactive={"trigger": "exit"})
        p_block, p_conv, p_open = _init_patches()
        with p_block, p_conv, p_open as opener:
            await chat_init(payload, _request_mock(), None)
        assert opener.await_args.args[3] == expected

    @pytest.mark.asyncio
    async def test_off_means_a_plain_chat(self):
        payload = ChatInitRequest(visitor_id=_VISITOR, proactive={"trigger": "kayak"})
        p_block, p_conv, p_open = _init_patches()
        with p_block, p_conv, p_open as opener:
            res = await chat_init(payload, _request_mock(), None)
        assert res.conversation_id == _CONV_ID and res.opening is None
        opener.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_support_tunnel_is_never_proactive(self, monkeypatch):
        _switch_on(monkeypatch)
        payload = ChatInitRequest(tunnel="support", visitor_id=_VISITOR, proactive={"trigger": "kayak"})
        p_block, p_conv, p_open = _init_patches()
        with p_block, p_conv, p_open as opener:
            res = await chat_init(payload, _request_mock(), None)
        assert res.opening is None
        opener.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unknown_trigger_or_bad_trip_never_costs_the_chat(self, monkeypatch):
        """A newer widget's trigger, or a trip that is not an object: the chat
        opens plain — never a 422."""
        _switch_on(monkeypatch)
        payload = ChatInitRequest(visitor_id=_VISITOR, trip=["x"], proactive={"trigger": "scroll_depth"})
        assert payload.trip is None and payload.proactive is None
        p_block, p_conv, p_open = _init_patches()
        with p_block, p_conv, p_open as opener:
            res = await chat_init(payload, _request_mock(), None)
        assert res.conversation_id == _CONV_ID and res.opening is None
        opener.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_opening_still_returns_the_chat(self, monkeypatch, caplog):
        _switch_on(monkeypatch)
        payload = ChatInitRequest(visitor_id=_VISITOR, proactive={"trigger": "quote"})
        p_block, p_conv, p_open = _init_patches(open_side_effect=RuntimeError("db down"))
        with p_block, p_conv, p_open, caplog.at_level(logging.ERROR):
            res = await chat_init(payload, _request_mock(), None)
        assert res.conversation_id == _CONV_ID and res.opening is None and res.opening_at is None
        assert proactive.PROACTIVE_HEALTH["open_failures"] == 1
        assert "proactive opening failed" in caplog.text


# ══════════════════════════════════════════════════════════════
# 8. Metadata allowlist and /health
# ══════════════════════════════════════════════════════════════

class TestPosthogSessionId:
    def test_a_real_id_is_kept(self):
        from app.security.metadata import allowed_widget_metadata
        sid = "0192f3a4-5b6c-7d8e-9f01-23456789abcd"
        assert allowed_widget_metadata({"posthog_session_id": f" {sid} "})["posthog_session_id"] == sid

    @pytest.mark.parametrize("bad", ["short", "a" * 65, "<script>alert(1)</script>xx", "0192f3a4 5b6c 7d8e", 12345678901234567])
    def test_anything_else_is_dropped(self, bad):
        from app.security.metadata import allowed_widget_metadata
        assert "posthog_session_id" not in allowed_widget_metadata({"posthog_session_id": bad})

    def test_server_keys_cannot_be_sent_by_the_widget(self):
        from app.security.metadata import allowed_widget_metadata
        out = allowed_widget_metadata({"proactive_trigger": "kayak", "proactive_trip": {"x": 1}})
        assert "proactive_trigger" not in out and "proactive_trip" not in out


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_shows_the_proactive_counters(self):
        from app.api.health import health_detail

        with patch("app.api.health.get_scheduler_health", return_value={}), \
             patch("app.api.health.supervisor_columns_status", return_value=True), \
             patch("app.api.health.settings") as s:
            s.debug = False
            payload = await health_detail()
        assert set(payload["proactive"]) == {
            "offers", "shown", "opened", "open_failures", "seed_errors", "last_seed_error_at",
        }
