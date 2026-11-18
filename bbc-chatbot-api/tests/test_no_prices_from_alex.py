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
])
def test_an_amount_in_any_currency_is_a_price(text):
    assert has_price(text)


@pytest.mark.parametrize("text", [
    "Our customers typically save 30-60% compared to retail prices.",
    "New York → London, Dec 10 – Dec 17, 2 adults in Business.",
    "We are IATA #14531683 accredited. Call +1 (888) 322-7999.",
    "Flight time is 7-8 hours; 2 bags included.",
])
def test_percentages_dates_counts_and_phones_are_not_prices(text):
    assert not has_price(text)


def test_a_sentence_with_an_amount_is_dropped_and_the_reply_ends_with_the_consultant_line():
    out, dropped = scrub_prices("London typically runs $2,800-$4,500. When are you looking to travel?")
    assert dropped == 1
    assert "$" not in out and "2,800" not in out
    assert out == f"When are you looking to travel? {PRICE_LINE}"


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
    assert _stream(g, ["Dec 10 for 2", " adults — noted. ", "An agent checks fares."]) == \
        "Dec 10 for 2 adults — noted. An agent checks fares."


def test_log_mode_stream_still_sends_text_without_digits_chunk_by_chunk(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "log")
    g = StreamGuard()
    assert [g.feed(c) for c in ["Happy to ", "help."]] == ["Happy to ", "help."]


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
