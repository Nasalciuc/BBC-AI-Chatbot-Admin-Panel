"""Alex never states a price (owner, 18 Nov 2026): only a consultant, who sees the live fares for the
client's dates, ever says an amount.

Three layers: the prompt forbids any price, range or amount; no amount from the knowledge base reaches
the model; and the reply guard drops, in every OUTPUT_GUARD_MODE, a sentence that names an amount —
stored replies end with PRICE_LINE, streamed sentences are held from their first digit to their end."""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.ai import output_guard
from app.ai.output_guard import PRICE_LINE, StreamGuard, has_price, scrub_prices, strip_amounts
from app.ai.prompts import COMMON_RULES, build_conversational_prompt
from app.models.chat import VisitorInfo
from app.models.kb import KBResult


@pytest.fixture(autouse=True)
def _clean():
    flags._reset_for_tests()
    output_guard._reset_for_tests()
    yield
    flags._reset_for_tests()


@pytest.mark.parametrize("text", [
    "Business class there typically runs $2,800-$4,500.",
    "That's 3,200 dollars for two.",
    "USD 4,500 is usual.",
    "Around €2.5k per person.",
    "About £900 each way.",
    "Fares start at US$ 1,999.",
    # The sign or the word after the amount, and the client's languages (CodeRabbit #270).
    "Le tarif est de 3.200 €.",
    "That's 900 US dollars.",
    "Around 900 U.S. dollars each.",
    "Unos 2.000 dólares por persona.",
    "Cerca de R$ 3.200 por pessoa.",
    "Thirty is 30 pounds sterling.",
    # Every currency clients write in (CodeRabbit #270, round 2).
    "The fare is CHF 3,200.",
    "Unos ¥320,000 ida y vuelta.",
    "Son 3,200 pesos por persona.",
    "About 45,000 rupees, or Rs. 45,000.",
    "AED 9,000 per person.",
    "Cirka 3000 kr per person.",
])
def test_an_amount_in_any_currency_is_a_price(text):
    assert has_price(text)


@pytest.mark.parametrize("text", [
    "Our customers typically save 30-60% compared to retail prices.",
    "New York → London, Dec 10 – Dec 17, 2 adults in Business.",
    "We are IATA #14531683 accredited. Call +1 (888) 322-7999.",
    "Flight time is 7-8 hours; 2 bags included.",
    # A weight is no price: baggage answers must stay.
    "Two checked bags of 23 kg (50 pounds) each.",
    "Cada 2 horas sale un vuelo a Europa.",
    # Currency codes that are everyday words are no currency here.
    "We won 3 awards for service.",
    "Let's try 2 dates in March.",
    "There are 3 real options this week.",
])
def test_percentages_dates_counts_and_phones_are_not_prices(text):
    assert not has_price(text)


def test_a_sentence_with_an_amount_is_dropped_and_the_reply_ends_with_the_consultant_line():
    out, dropped = scrub_prices("London typically runs $2,800-$4,500. When are you looking to travel?")
    assert dropped == 1
    assert "$" not in out and "2,800" not in out
    assert out == f"When are you looking to travel? {PRICE_LINE}"


def test_an_amount_split_by_a_line_break_still_takes_both_lines():
    out, dropped = scrub_prices("The fare is 3,200\nUSD per person. When do you fly?")
    assert dropped == 2
    assert "3,200" not in out and "USD" not in out
    assert out == f"When do you fly? {PRICE_LINE}"


def test_a_reply_that_is_only_a_price_becomes_the_consultant_line():
    assert scrub_prices("It's €2,100.")[0] == PRICE_LINE


def test_a_reply_without_an_amount_is_unchanged():
    text = "Got it — an agent checks live fares for exactly this trip. Where should they send them?"
    assert scrub_prices(text) == (text, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["log", "enforce"])
async def test_the_stored_reply_never_keeps_an_amount_in_any_mode(monkeypatch, mode):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", mode)
    with patch("app.services.email.send_ops_alert_email", new=AsyncMock()):
        out = await output_guard.guard_ai_text("Expect about $3,100 round trip. Your dates?", conversation_id="c1")
    assert "3,100" not in out and "$" not in out
    assert out.endswith(PRICE_LINE)
    assert output_guard.OUTPUT_GUARD_HEALTH["prices_removed"] >= 1


def _stream(g, chunks):
    return "".join(g.feed(c) for c in chunks) + g.flush()


def test_log_mode_stream_holds_a_sentence_from_its_first_digit_and_drops_an_amount(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "log")
    g = StreamGuard()
    out = _stream(g, ["Great choice. It runs ", "$2,8", "00 typically. ", "When do you fly?"])
    assert "2,8" not in out and "$" not in out
    assert out.startswith("Great choice. ") and out.endswith("When do you fly?")


def test_log_mode_stream_sends_a_sentence_with_digits_but_no_amount(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "log")
    g = StreamGuard()
    # The long dash goes out as a comma (20 Nov: it reads as a machine's), the stored reply's way.
    assert _stream(g, ["Dec 10 for 2", " adults — noted. ", "An agent checks fares."]) == \
        "Dec 10 for 2 adults, noted. An agent checks fares."


def test_log_mode_stream_still_sends_text_without_digits_chunk_by_chunk(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "log")
    g = StreamGuard()
    # Each chunk goes out as it comes; only the space after a word waits for the next chunk, in case that
    # one starts with a long dash (" —" goes out as ",").
    assert [g.feed(c) for c in ["Happy to ", "help."]] == ["Happy to", " help."]


def test_log_mode_stream_keeps_a_split_currency_word_until_it_is_whole(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "log")
    g = StreamGuard()
    out = _stream(g, ["The fare is US", "D 4,500. ", "When do you fly?"])
    assert "4,500" not in out and "USD" not in out
    assert out.endswith("When do you fly?")


def test_log_mode_stream_does_not_end_an_amount_at_a_line_break(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "log")
    g = StreamGuard()
    out = _stream(g, ["The fare is 3,200\n", "USD per person. ", "Your dates?"])
    assert "3,200" not in out and "USD" not in out
    assert out.endswith("Your dates?")


def test_enforce_stream_drops_an_amount_split_by_a_line_break(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "enforce")
    g = StreamGuard()
    out = _stream(g, ["The fare is 3,200\n", "USD per person. ", "Your dates? "])
    assert "3,200" not in out and "USD" not in out and "Your dates?" in out


@pytest.mark.parametrize("chunks", [
    ["The fare is CH", "F 3,200. ", "Your dates?"],
    ["Son unos ", "¥320,000. ", "Your dates?"],
    ["Son 3,200 pe", "sos. ", "Your dates?"],
])
def test_log_mode_stream_drops_every_currency_even_split(monkeypatch, chunks):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "log")
    out = _stream(StreamGuard(), chunks)
    assert "3,200" not in out and "320,000" not in out and out.endswith("Your dates?")


def test_the_stored_reply_drops_every_currency():
    for text in ("The fare is CHF 3,200. Your dates?", "Son 3,200 pesos. Your dates?"):
        out, dropped = scrub_prices(text)
        assert dropped == 1 and out == f"Your dates? {PRICE_LINE}"


def test_enforce_stream_drops_a_piece_with_an_amount(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "enforce")
    g = StreamGuard()
    out = _stream(g, ["Fares run 3,000 dollars. ", "Your consultant confirms the rest. "])
    assert "3,000" not in out and "Your consultant confirms the rest." in out


def test_knowledge_base_amounts_and_ranges_never_reach_the_model():
    kb = KBResult(entry_id="k1", title="New York to London",
                  content="Business class typically $2,800-$4,500 round trip. Peak: Jun-Aug. From 3,200 USD.")
    _static, dynamic = build_conversational_prompt("sales", VisitorInfo(), kb_results=[kb])
    whole = _static + dynamic
    assert "2,800" not in whole and "4,500" not in whole and "3,200 USD" not in whole
    assert "Peak: Jun-Aug" in whole
    assert strip_amounts("Typically $2,800–$4,500.") == "Typically (fares: quoted only by a consultant)."
    # One currency for both ends of a range takes both ends; a range of hours is no amount.
    assert strip_amounts("Typically 2,800–4,500 USD.") == "Typically (fares: quoted only by a consultant)."
    assert strip_amounts("Le vol dure 7-8 heures, dès 3.200 €.") == "Le vol dure 7-8 heures, dès (fares: quoted only by a consultant)."
    assert strip_amounts("Desde ¥320,000 o 3,200 pesos.") == \
        "Desde (fares: quoted only by a consultant) o (fares: quoted only by a consultant)."
    # A day beside a fare is no end of a range: the date stays (CodeRabbit #270, round 2).
    assert strip_amounts("Departure: Dec 10 — $500 per person.") == \
        "Departure: Dec 10 — (fares: quoted only by a consultant) per person."
    assert strip_amounts("Fares 500-700 USD.") == "Fares (fares: quoted only by a consultant)."


def test_a_knowledge_base_title_never_carries_an_amount_to_the_model():
    kb = KBResult(entry_id="k2", title="London from $7,345", content="Daily flights from New York.")
    _static, dynamic = build_conversational_prompt("sales", VisitorInfo(), kb_results=[kb])
    assert "7,345" not in _static + dynamic
    assert "• London from (fares: quoted only by a consultant): Daily flights from New York." in dynamic


def test_the_prompt_forbids_any_price_range_or_amount():
    rules = COMMON_RULES
    assert "ALWAYS use ranges" not in rules
    assert "NEVER state a price, a fare, a range or any amount of money" in rules
    static, dynamic = build_conversational_prompt("sales", VisitorInfo())
    whole = static + dynamic
    assert "ranges only" not in whole
    assert "$1,000" not in whole  # the flexibility probe no longer names an amount


def test_the_route_card_never_carries_a_price_range():
    from app.pipeline.generator import _build_route_card_from_kb
    kb = KBResult(entry_id="k2", title="New York to London",
                  content="Airlines: BA, AA. Duration: 7-8 hours nonstop. Business class typically $2,800-$4,500 (JFK) (LHR).")
    card = _build_route_card_from_kb(kb)
    assert card is not None and card.price_range == ""


def test_the_route_card_never_carries_an_amount_from_a_title_or_the_airlines():
    """A KB title "New York to London from $7,345" gave the card a destination with the fare in it
    (CodeRabbit #270, round 2)."""
    from app.ai.output_guard import without_amounts
    from app.pipeline.generator import _build_route_card_from_kb

    card = _build_route_card_from_kb(KBResult(
        entry_id="k3", title="New York to London from $7,345",
        content="Nonstop flights from New York. Airlines: BA, AA from $3,000."))
    assert card is not None
    assert (card.origin, card.destination, card.airlines) == ("New York", "London", "BA, AA")
    plain = _build_route_card_from_kb(KBResult(entry_id="k4", title="New York to London",
                                               content="Nonstop flights. Airlines: BA, AA."))
    assert (plain.origin, plain.destination, plain.airlines) == ("New York", "London", "BA, AA")
    assert without_amounts("Paris desde 2.000 €") == "Paris"
    assert without_amounts("Tokyo") == "Tokyo"


@pytest.mark.parametrize("mode", ["log", "enforce"])
@pytest.mark.parametrize("chunks", [
    ["Rs. ", "45,000. ", "Your dates? "],
    ["That's 900 U.S. ", "dollars each. ", "Your dates? "],
])
def test_a_currency_abbreviation_ends_no_sentence_in_the_stream(monkeypatch, mode, chunks):
    """"Rs. " then "45,000. ": cut at the abbreviation's full stop, each piece looked harmless and the
    amount went out (CodeRabbit #270, round 3)."""
    monkeypatch.setenv("OUTPUT_GUARD_MODE", mode)
    out = _stream(StreamGuard(), chunks)
    assert "45,000" not in out and "900" not in out
    assert out.strip().endswith("Your dates?")


def test_the_stored_reply_drops_an_amount_after_an_abbreviation():
    out, dropped = scrub_prices("Rs. 45,000. Your dates?")
    assert "45,000" not in out and out == f"Your dates? {PRICE_LINE}"


def test_a_short_range_end_goes_with_its_amount():
    """"$50–75" left "–75" behind (CodeRabbit #270, round 3); a count after an amount is no range end, and
    a day before one stays."""
    note = "(fares: quoted only by a consultant)"
    assert strip_amounts("Fares $50–75.") == f"Fares {note}."
    assert strip_amounts("Snacks €9.99-14.99 on board.") == f"Snacks {note} on board."
    assert strip_amounts("$50 - 2 adults") == f"{note} - 2 adults"
    assert strip_amounts("Departure: Dec 10 — $500 per person.") == f"Departure: Dec 10 — {note} per person."
    assert strip_amounts("Typically $2,800–$4,500.") == f"Typically {note}."
    # A unit or a count after the short number keeps it (CodeRabbit #270, round 4); a fare range still goes.
    assert strip_amounts("Lounge access $50 - 90 minutes.") == f"Lounge access {note} - 90 minutes."
    assert strip_amounts("$5 - 10 adults") == f"{note} - 10 adults"
    assert strip_amounts("From $50–75 per person.") == f"From {note} per person."


def test_the_route_card_never_carries_a_short_range():
    from app.ai.output_guard import without_amounts
    from app.pipeline.generator import _build_route_card_from_kb

    assert without_amounts("London from $50–75") == "London"
    card = _build_route_card_from_kb(KBResult(entry_id="k5", title="New York to London from $50–75",
                                              content="Nonstop flights. Airlines: BA, AA."))
    assert card is not None and card.destination == "London"
