"""The visitors who decide in seconds (20 Nov 2026): the ads card reads the ad's search, the card says the
cabin that was searched, a one-way trip is said as one, and the phone card leads with the trip.

ADR A3–A7 of "Vizitatorii rapizi de la Kayak și Bing". The places and keywords below are made up for the
tests; none is a client's.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.models.trip_context import TripContext
from app.services import proactive, proactive_i18n

VISITOR = "visitor-0123456789"


def _day(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def _kayak(**kw) -> TripContext:
    base = dict(source="kayak", origin_code="AAA", origin_name="Springfield", destination_code="BBB",
                destination_name="Lakeside", depart_date=_day(40), cabin="business", adults=1)
    base.update(kw)
    return TripContext(**base)


# --- the keyword itself ----------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, kept", [
    ("First Class Flights to Lakeside", "first class flights to lakeside"),
    ("  cheap   business+class to <b>lakeside</b> ", "cheap business class to b lakeside b"),
    ("120253513391840723", None),            # an ad id, not a search
    ("ab", None),
    ("x" * 81, None),
    (42, None),
])
def test_the_keyword_is_kept_only_as_plain_lower_case_words(raw, kept):
    assert TripContext(keyword=raw).keyword == kept


def test_the_keyword_is_no_trip_and_never_reaches_a_lead():
    trip = TripContext(source="route_page", keyword="first class flights to lakeside")
    assert trip.is_empty()
    assert proactive._entities_from_trip(trip) == {}


# --- the place and the cabin it names --------------------------------------------------------------------

@pytest.mark.parametrize("keyword, place, cabin", [
    ("first class flights to lakeside", "Lakeside", "first"),
    ("cheap business class to lakeside", "Lakeside", "business"),
    ("business class flights to port lakeside italy", "Port Lakeside", "business"),    # the country after it goes
    ("first class flight to lakeside scotland", "Lakeside", "first"),
    ("flights to new lakeside in december", "New Lakeside", None),                  # the month ends the place
    ("flight deals to lakeside", "Lakeside", None),
    ("1st class flights to lakeside", "Lakeside", "first"),
    ("air france business class to lakeside", "Lakeside", "business"),             # the airline is no place
    ("air france business class", None, "business"),                              # nothing after "to"
    ("lakeside", None, None),                                                     # one word: no "to"
    ("business class to the usa from lakeside", None, "business"),                 # the visitors' own country
    ("business class flights to marrakesh", "Marrakesh", "business"),              # "mar…" is no month
    ("flights to x", None, None),
    # CodeRabbit #272: the place follows "to" when both are there; "in" alone still names one.
    ("flights in business class to lakeside", "Lakeside", "business"),
    ("business class in lakeside", "Lakeside", "business"),
    # …and an economy search is one (premium economy before economy: it says both).
    ("premium economy flights to lakeside", "Lakeside", "premium_economy"),
    ("economy flights to lakeside", "Lakeside", "economy"),
    ("flights to west new lake shore", None, None),                              # four words: no name
])
def test_the_place_is_read_only_after_to_and_the_cabin_from_its_words(keyword, place, cabin):
    assert proactive.keyword_place(keyword) == place
    assert proactive.keyword_cabin(keyword) == cabin


def test_the_keyword_names_the_place_only_when_the_page_names_none():
    page = TripContext(source="route_page", destination_name="Hilltown", cabin="business",
                       keyword="first class flights to lakeside")
    trip = proactive.with_keyword(page)
    assert trip.destination_name == "Hilltown"     # the page's place stays
    assert trip.cabin == "first"                   # the cabin searched wins over the page's
    home = proactive.with_keyword(TripContext(source="route_page", keyword="first class flights to lakeside"))
    assert home.destination_name == "Lakeside"


def test_a_trip_without_a_keyword_comes_back_as_it_was():
    trip = _kayak()
    assert proactive.with_keyword(trip) is trip
    assert proactive.with_keyword(None) is None


# --- the words -------------------------------------------------------------------------------------------

def test_the_ads_card_says_the_cabin_that_was_searched():
    trip = TripContext(source="route_page", destination_name="Lakeside", cabin="business",
                       keyword="first class flights to lakeside")
    text, _ = proactive.compose("ads", trip, "bbc")
    assert "first class fares" in text and "business class" not in text


def test_without_a_keyword_the_english_words_are_word_for_word_as_before():
    trip = TripContext(source="route_page", destination_name="Lakeside", cabin="business")
    text, _ = proactive.compose("ads", trip, "bbc")
    assert text == ("Hi, I'm Alex from BuyBusinessClass. Planning a trip to Lakeside? Tell me roughly when, "
                    "and an agent will check business class fares for you.")
    dated = TripContext(source="route_page", destination_name="Lakeside", depart_date=_day(40))
    text, _ = proactive.compose("reader", dated, "bbc")
    assert text.endswith("Want an agent to check business class fares for these dates?")


@pytest.mark.parametrize("cabin, lang, words", [
    ("premium_economy", None, "premium economy fares"), ("economy", None, "economy fares"),
    ("premium_economy", "es", "tarifas en Premium Economy"), ("economy", "fr", "les tarifs en classe Économique"),
])
def test_an_economy_search_on_a_business_page_is_not_answered_with_business_fares(cabin, lang, words):
    keyword = "premium economy flights to lakeside" if cabin == "premium_economy" else "economy flights to lakeside"
    trip = TripContext(source="route_page", destination_name="Lakeside", cabin="business", keyword=keyword)
    text, _ = proactive.compose("ads", trip, "bbc", lang=lang)
    assert words in text
    assert proactive.card_face("ads", trip, lang=lang)["headline"].endswith(
        proactive_i18n.TITLE_CABINS[lang or "en"][cabin])


@pytest.mark.parametrize("lang, first", [("es", "en Primera clase"), ("fr", "en Première classe")])
def test_the_cabin_is_said_in_the_cards_language(lang, first):
    trip = TripContext(source="route_page", destination_name="Lakeside", cabin="first")
    text, _ = proactive.compose("ads", trip, "bbc", lang=lang)
    assert first in text


def test_only_the_ads_card_reads_the_keyword():
    # The reader on the same visit keeps the page's words: the keyword is the ad's, read by the ad's card.
    trip = TripContext(source="route_page", keyword="first class flights to lakeside")
    text, _ = proactive.compose("reader", trip, "bbc")
    assert "Lakeside" not in text and "Planning a business class trip?" in text


@pytest.mark.parametrize("lang, word", [(None, "one-way"), ("es", "solo ida"), ("fr", "aller simple")])
def test_a_one_way_search_is_said_as_one(lang, word):
    line = proactive.trip_line(_kayak(trip_type="one_way"), lang)
    assert word in line
    assert word not in proactive.trip_line(_kayak(trip_type="round_trip", return_date=_day(47)), lang)


# --- the offer -------------------------------------------------------------------------------------------

def test_an_ads_search_that_names_no_place_switches_off_this_visits_ads_moment():
    trip = TripContext(source="route_page", keyword="business class tickets for sale")
    out = proactive.offer(VISITOR, "ads", trip, "bbc", device="phone")
    assert out["show"] is False and out["arm"] == proactive.TRIGGER_OFF and out["reason"] == "no_place"


def test_an_ads_search_that_names_a_place_gets_the_card_its_title_and_its_channel():
    trip = TripContext(source="route_page", keyword="first class flights to lakeside")
    out = proactive.offer(VISITOR, "ads", trip, "bbc", device="phone")
    assert out["show"] is True
    assert out["headline"] == "Lakeside · First class"
    assert out["card_text"] == "Tell me roughly when, and an agent will check first class fares for you."
    assert out["message"].endswith(out["card_text"])
    assert out["channel"] == {"kind": "sms", "label": "Fares by text"}
    assert "confirm" not in out            # no dates: no instant answer to the yes
    computer = proactive.offer(VISITOR, "ads", trip, "bbc", device="computer")
    assert computer["channel"]["kind"] == "email"


def test_an_ads_card_on_a_page_with_a_place_and_no_keyword_still_shows():
    trip = TripContext(source="route_page", destination_name="Lakeside", cabin="business")
    out = proactive.offer(VISITOR, "ads", trip, "bbc", device="phone")
    assert out["show"] is True and out["headline"] == "Lakeside · Business class"


@pytest.mark.parametrize("lang", [None, "es", "fr"])
def test_the_kayak_card_leads_with_the_trip_and_asks_only_its_question(lang):
    trip = _kayak(return_date=_day(47), adults=2, cabin="first")
    out = proactive.offer(VISITOR, "kayak", trip, "bbc", device="phone", lang=lang)
    assert out["headline"].startswith("Springfield → Lakeside · ")
    assert out["headline"].endswith(proactive_i18n.TITLE_CABINS[lang or "en"]["first"])
    # The card's question is the opening's own last sentence: the chat still opens with the whole of it.
    assert out["message"].endswith(out["card_text"])
    assert out["card_text"] == proactive_i18n.sentence(proactive_i18n.card_lang(lang), "ask_full")


def test_the_beat_card_asks_its_own_question():
    trip = _kayak(return_date=_day(47))
    face = proactive.card_face("kayak", trip, proactive.BEAT)
    text, _ = proactive.compose("kayak", trip, "bbc", proactive.BEAT)
    assert face["card_text"].startswith("Already seen a price?")
    assert text.endswith(face["card_text"])


def test_one_traveller_is_not_in_the_title_and_one_way_is():
    face = proactive.card_face("kayak", _kayak(trip_type="one_way"))
    assert face["headline"] == f"Springfield → Lakeside · {proactive._dates(_kayak())} · Business class · one-way"


@pytest.mark.parametrize("lang", [None, "es", "fr"])
def test_the_longest_title_a_trip_allows_still_fits_what_the_widget_takes(lang):
    # Two 40-character places, both dates, six travellers, the longest cabin and one-way (CodeRabbit #272).
    trip = TripContext(source="kayak", origin_name="A" * 40, destination_name="B" * 40, depart_date=_day(40),
                       return_date=_day(49), trip_type="one_way", cabin="premium_economy", adults=2, children=2,
                       infants=2)
    title = proactive.card_face("kayak", trip, lang=lang)["headline"]
    assert len(title) <= proactive.TITLE_MAX == 240     # bbc-widget api.ts TITLE_MAX: the same number


def test_a_title_over_the_cap_is_no_title(monkeypatch):
    monkeypatch.setattr(proactive, "TITLE_MAX", 10)
    assert proactive.card_face("kayak", _kayak()) is None


@pytest.mark.parametrize("trigger", ["quote", "reader", "note", "exit"])
def test_the_other_cards_keep_their_whole_message(trigger):
    assert proactive.card_face(trigger, _kayak()) is None


def test_a_kayak_card_without_a_destination_has_no_title():
    assert proactive.card_face("kayak", TripContext(source="kayak", origin_code="AAA", depart_date=_day(40))) is None


@pytest.mark.parametrize("key, ask", [
    ("full", "ask_full"), ("full_beat", "ask_full_beat"), ("dest_date", "ask_dest_date"), ("dest", "ask_dest"),
])
@pytest.mark.parametrize("lang", ["en", "es", "fr"])
def test_every_question_ends_its_sentence_in_every_language(key, ask, lang):
    whole = proactive_i18n.SENTENCES[lang][key]
    assert whole.endswith(proactive_i18n.SENTENCES[lang][ask])


# --- the lead the ads card opens (CodeRabbit #272) ---------------------------------------------------------

@pytest.mark.asyncio
async def test_the_ads_opening_and_its_lead_say_the_same_cabin_and_place():
    """A business class page visited for "first class flights to lakeside": the opening says first class
    fares, and the lead is seeded with first and the place, not the page's business and nothing."""
    trip = TripContext(source="route_page", cabin="business", keyword="first class flights to lakeside")
    with patch("app.db.supabase.count_messages", new=AsyncMock(return_value=0)), \
         patch("app.services.proactive._claim_opening", new=AsyncMock(return_value=True)), \
         patch("app.services.conversation_service.add_message",
               new=AsyncMock(return_value={"id": "m1", "content": "x", "created_at": None})) as add, \
         patch("app.db.supabase._merge_conv_metadata", new=AsyncMock()) as meta, \
         patch("app.services.proactive.seed_lead", new=AsyncMock()) as seed:
        await proactive.open_conversation("conv-1", "ads", trip)
    assert "first class fares" in add.await_args.args[2]
    seeded = seed.await_args.args[1]
    assert seeded.cabin == "first" and seeded.destination_name == "Lakeside"
    assert proactive._entities_from_trip(seeded)["cabin_class"] == "first"
    assert meta.await_args.args[1]["proactive_trip"]["cabin"] == "first"


@pytest.mark.asyncio
async def test_another_cards_opening_seeds_the_page_trip_as_it_was():
    trip = TripContext(source="route_page", destination_name="Lakeside", cabin="business",
                       keyword="first class flights to lakeside")
    with patch("app.db.supabase.count_messages", new=AsyncMock(return_value=0)), \
         patch("app.services.proactive._claim_opening", new=AsyncMock(return_value=True)), \
         patch("app.services.conversation_service.add_message",
               new=AsyncMock(return_value={"id": "m1", "content": "x", "created_at": None})), \
         patch("app.db.supabase._merge_conv_metadata", new=AsyncMock()), \
         patch("app.services.proactive.seed_lead", new=AsyncMock()) as seed:
        await proactive.open_conversation("conv-1", "reader", trip)
    assert seed.await_args.args[1] is trip


@pytest.mark.asyncio
async def test_the_ads_search_itself_never_reaches_the_lead():
    """CodeRabbit #272, round 3: the place and the cabin read from the search are on the lead's page trip;
    the words typed are not."""
    from unittest.mock import MagicMock

    trip = proactive.with_keyword(TripContext(source="route_page", keyword="first class flights to lakeside"))
    assert trip.keyword  # the trip the card spoke of still carries it: only the lead leaves it out
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
        MagicMock(data=[{"id": "L1", "trip_notes": {}, "cabin_class": "business"}]))
    written = []
    client.table.return_value.update.side_effect = lambda payload: written.append(payload) or MagicMock()

    async def run_sync(fn):
        return fn()

    with patch("app.db.supabase.get_client", return_value=client), patch("app.db.supabase._run_sync", new=run_sync):
        await proactive._mark_page_trip("conv-1", trip)
    page = written[0]["trip_notes"]["page_trip"]
    assert "keyword" not in page and "flights" not in str(page)
    assert page["destination_name"] == "Lakeside" and page["cabin"] == "first"
