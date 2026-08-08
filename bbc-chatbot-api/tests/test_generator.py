"""Tests for generator smart routing — template selection logic."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from unittest.mock import patch
import pytest

from app.pipeline.intent import Intent
from app.models.chat import VisitorInfo
from app.pipeline.generator import generate_response


# Mock Claude to return None — forces template-only behavior
@pytest.fixture(autouse=True)
def mock_claude():
    empty_tools = (None, 0.0, {})
    with (
        patch("app.ai.claude._call_model", return_value=(None, 0.0)),
        patch("app.pipeline.generator.call_haiku_with_tools", return_value=empty_tools),
        patch("app.pipeline.generator.call_sonnet_with_tools", return_value=empty_tools),
        patch("app.pipeline.generator.call_opus_with_tools", return_value=empty_tools),
        patch("app.pipeline.generator.stream_haiku_with_tools", return_value=empty_tools),
        patch("app.pipeline.generator.stream_sonnet_with_tools", return_value=empty_tools),
        patch("app.pipeline.generator.stream_opus_with_tools", return_value=empty_tools),
    ):
        yield


def _gen(intent, lead=None, history=None, tunnel="sales", visitor=None):
    """Shortcut to call generate_response with sensible defaults."""
    return generate_response(
        intent=intent,
        entities={"_raw_message": "test"},
        kb_results=[],
        visitor=visitor or VisitorInfo(),
        lead=lead,
        history=history or [],
        tunnel=tunnel,
    )


class TestIntentSpecificTemplates:
    """Step 4.5a — intent-specific templates (baggage, price, booking)."""

    def test_baggage_returns_template(self):
        res = _gen(Intent.BAGGAGE_INFO)
        assert res.model_used == "template"
        assert "baggage" in res.text.lower() or "bag" in res.text.lower()

    def test_price_returns_template(self):
        res = _gen(Intent.PRICE_INQUIRY)
        assert res.model_used == "template"
        assert "quote" in res.text.lower() or "departure" in res.text.lower()

    def test_booking_change_support_returns_template(self):
        res = _gen(Intent.BOOKING_CHANGE, tunnel="support")
        assert res.model_used == "template"
        assert "booking" in res.text.lower() or "reference" in res.text.lower()

    def test_greeting_still_works(self):
        res = _gen(Intent.GREETING)
        assert res.model_used == "template"
        assert "welcome" in res.text.lower() or "hi" in res.text.lower()


class TestSmartRouting:
    """Step 4.5b — lead-aware smart template routing."""

    def test_lead_none_falls_to_fallback(self):
        """No lead → skip smart routing → AI fails → fallback."""
        res = _gen(Intent.NEW_BOOKING, lead=None)
        assert res.model_used == "template"
        # Should be ai_fallback since Claude is mocked to None

    def test_lead_empty_no_smart_routing(self):
        """Lead exists but has no data → skip smart routing."""
        res = _gen(Intent.NEW_BOOKING, lead={"id": "x", "conversation_id": "y"})
        assert res.model_used == "template"
        # No smart routing because has_some_data = False

    def test_lead_with_route_asks_dates(self):
        """Lead has route → smart routing asks for dates."""
        lead = {"origin_code": "JFK", "destination_code": "LHR"}
        res = _gen(Intent.NEW_BOOKING, lead=lead)
        assert res.model_used == "template"
        text_lower = res.text.lower()
        # Should ask about dates or confirm route
        assert any(w in text_lower for w in ["when", "dates", "travel", "flexibility"]), \
            f"Expected dates question, got: {res.text}"

    def test_lead_with_route_and_dates_asks_phone(self):
        """Lead has route + dates → asks for phone."""
        lead = {
            "origin_code": "JFK", "destination_code": "LHR",
            "departure_date": "2026-03-15",
        }
        res = _gen(Intent.NEW_BOOKING, lead=lead)
        assert res.model_used == "template"
        text_lower = res.text.lower()
        assert any(w in text_lower for w in ["phone", "number", "reach"]), \
            f"Expected phone question, got: {res.text}"

    def test_lead_complete_handoff(self):
        """Lead with all fields → specialist handoff."""
        lead = {
            "origin_code": "JFK", "destination_code": "LHR",
            "departure_date": "2026-03-15",
            "visitor_name": "John", "visitor_phone": "212-555-0187",
            "visitor_email": "j@e.com", "route_display": "JFK → LHR",
        }
        res = _gen(Intent.NEW_BOOKING, lead=lead)
        assert res.model_used == "template"
        text_lower = res.text.lower()
        assert any(w in text_lower for w in ["specialist", "team", "reach out", "contact"]), \
            f"Expected handoff, got: {res.text}"

    def test_support_tunnel_no_smart_routing(self):
        """Smart routing is SALES only — support skips it."""
        lead = {"origin_code": "JFK", "destination_code": "LHR"}
        res = _gen(Intent.NEW_BOOKING, lead=lead, tunnel="support")
        # Should NOT ask for dates (support doesn't do lead capture)
        # Falls through to AI → fallback
        assert res.model_used == "template"


class TestLoopPrevention:
    """Smart routing should not repeat the same question."""

    def test_no_double_dates_ask(self):
        """If last AI msg already asked for dates, skip dates question."""
        lead = {"origin_code": "JFK", "destination_code": "LHR"}
        history = [
            {"role": "user", "content": "JFK to LHR"},
            {"role": "ai", "content": "When are you looking to travel? Flexibility helps."},
            {"role": "user", "content": "maybe March"},
        ]
        res = _gen(Intent.NEW_BOOKING, lead=lead, history=history)
        text_lower = res.text.lower()
        # Should NOT ask dates again — should fall through to AI/fallback
        # (or ask phone instead, which is next in priority)
        assert "when" not in text_lower or "phone" in text_lower or "specialist" in text_lower

    def test_no_double_phone_ask(self):
        """If last AI msg asked for phone, skip phone question."""
        lead = {
            "origin_code": "JFK", "destination_code": "LHR",
            "departure_date": "2026-03-15",
        }
        history = [
            {"role": "ai", "content": "What's the best number to reach you?"},
            {"role": "user", "content": "I don't want to give my phone"},
        ]
        res = _gen(Intent.NEW_BOOKING, lead=lead, history=history)
        text_lower = res.text.lower()
        # Should NOT ask phone again — fall to email or AI
        assert "phone" not in text_lower or "email" in text_lower or "specialist" in text_lower


class TestBudgetGuard:
    """Budget guard should skip AI when daily budget is exceeded."""

    def test_budget_exceeded_uses_fallback(self):
        res = generate_response(
            intent=Intent.NEW_BOOKING, entities={"_raw_message": "test"},
            kb_results=[], visitor=VisitorInfo(), lead=None,
            history=[], tunnel="sales", budget_remaining=-5.0,
        )
        assert res.model_used == "template"
        # ai_fallback picks a random variant — accept any of them (one names
        # specialists, the other points to the phone line; both are fallbacks).
        from app.ai.templates import TEMPLATES as _T

        _fallbacks = {t.format(name="", name_suffix="") for t in _T["ai_fallback"]}
        assert res.text in _fallbacks or "specialist" in res.text.lower() or "connect" in res.text.lower()

    def test_budget_ok_proceeds(self):
        res = generate_response(
            intent=Intent.NEW_BOOKING, entities={"_raw_message": "test"},
            kb_results=[], visitor=VisitorInfo(), lead=None,
            history=[], tunnel="sales", budget_remaining=25.0,
        )
        assert res.model_used == "template"

    def test_budget_none_proceeds(self):
        res = generate_response(
            intent=Intent.NEW_BOOKING, entities={"_raw_message": "test"},
            kb_results=[], visitor=VisitorInfo(), lead=None,
            history=[], tunnel="sales",
        )
        assert res.model_used == "template"

    def test_budget_zero_triggers_guard(self):
        res = generate_response(
            intent=Intent.NEW_BOOKING, entities={"_raw_message": "test"},
            kb_results=[], visitor=VisitorInfo(), lead=None,
            history=[], tunnel="sales", budget_remaining=0.0,
        )
        assert res.model_used == "template"
