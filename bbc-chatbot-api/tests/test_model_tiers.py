"""Tests for the three model tiers — utility / sales default / premium."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from unittest.mock import patch

import pytest

from app.ai import claude
from app.models.chat import VisitorInfo
from app.pipeline import generator
from app.pipeline.generator import (
    _generation_tier,
    _select_sales_model,
    generate_response,
)
from app.pipeline.intent import Intent
from config.settings import settings

GOLD = {"score": 85}
SILVER = {"score": 60}
BRONZE = {"score": 10}
FIRST_CABIN = {"score": 10, "cabin_class": "First"}
SEEKER = {"score": 10, "intent_signals": {"persona": "experience_seeker"}}


class TestSelectionTruthTable:
    """Which brain answers this turn."""

    def test_gold_lead_escalates(self):
        assert _select_sales_model(Intent.NEW_BOOKING, GOLD) == ("opus", "tier")

    def test_first_cabin_escalates(self):
        assert _select_sales_model(Intent.NEW_BOOKING, FIRST_CABIN) == ("opus", "cabin")

    def test_experience_seeker_escalates(self):
        assert _select_sales_model(Intent.NEW_BOOKING, SEEKER) == ("opus", "persona")

    @pytest.mark.parametrize(
        "intent",
        [Intent.PRICE_INQUIRY, Intent.TALK_TO_AGENT, Intent.BOOKING_CHANGE],
    )
    def test_decisive_intents_escalate_for_anyone(self, intent):
        assert _select_sales_model(intent, BRONZE) == ("opus", "intent")
        assert _select_sales_model(intent, None) == ("opus", "intent")

    def test_ordinary_turn_stays_sonnet(self):
        assert _select_sales_model(Intent.NEW_BOOKING, BRONZE) == ("sonnet", "default")
        assert _select_sales_model(Intent.GENERAL_QUESTION, SILVER) == ("sonnet", "default")

    def test_missing_lead_does_not_crash(self):
        assert _select_sales_model(Intent.NEW_BOOKING, None) == ("sonnet", "default")
        assert _select_sales_model(Intent.NEW_BOOKING, {}) == ("sonnet", "default")

    def test_null_lead_fields_do_not_crash(self):
        lead = {"score": None, "cabin_class": None, "intent_signals": None}
        assert _select_sales_model(Intent.NEW_BOOKING, lead) == ("sonnet", "default")

    def test_support_tunnel_never_escalates(self):
        assert _generation_tier(Intent.PRICE_INQUIRY, GOLD, "support") == (
            "haiku",
            "support_tunnel",
        )

    def test_sales_tunnel_uses_selection(self):
        assert _generation_tier(Intent.NEW_BOOKING, GOLD, "sales") == ("opus", "tier")


def _mock_tiers(text="ok", entities=None):
    """Patch every tier call in the generator; returns the mock dict."""
    names = [
        "call_haiku_with_tools",
        "call_sonnet_with_tools",
        "call_opus_with_tools",
        "stream_haiku_with_tools",
        "stream_sonnet_with_tools",
        "stream_opus_with_tools",
    ]
    patches = {
        name: patch.object(
            generator, name, return_value=(text, 0.01, entities or {})
        )
        for name in names
    }
    return patches


class _TierHarness:
    """Enter all tier patches at once and expose them by name."""

    def __init__(self, text="ok", entities=None):
        self._patches = _mock_tiers(text, entities)
        self.mocks = {}

    def __enter__(self):
        for name, p in self._patches.items():
            self.mocks[name] = p.start()
        return self.mocks

    def __exit__(self, *exc):
        for p in self._patches.values():
            p.stop()
        return False


def _gen(intent, lead=None, tunnel="sales", on_chunk=None, raw="test", **kwargs):
    return generate_response(
        intent=intent,
        entities={"_raw_message": raw},
        kb_results=[],
        visitor=VisitorInfo(),
        lead=lead,
        history=[{"role": "user", "content": raw}],
        tunnel=tunnel,
        on_chunk=on_chunk,
        **kwargs,
    )


class TestAiFirstSiteRouting:
    """Site 1 — the AI-first path every normal turn takes."""

    def test_default_sales_turn_uses_sonnet(self):
        with _TierHarness() as m:
            res = _gen(Intent.NEW_BOOKING, lead=BRONZE)
        assert res.model_used == "sonnet"
        assert m["call_sonnet_with_tools"].called
        assert not m["call_haiku_with_tools"].called
        assert not m["call_opus_with_tools"].called

    def test_gold_lead_uses_opus(self):
        with _TierHarness() as m:
            res = _gen(Intent.NEW_BOOKING, lead=GOLD)
        assert res.model_used == "opus"
        assert m["call_opus_with_tools"].called

    def test_support_tunnel_uses_haiku(self):
        with _TierHarness() as m:
            res = _gen(Intent.GENERAL_QUESTION, lead=GOLD, tunnel="support")
        assert res.model_used == "haiku"
        assert m["call_haiku_with_tools"].called

    def test_streaming_routes_to_matching_stream(self):
        with _TierHarness() as m:
            res = _gen(Intent.NEW_BOOKING, lead=GOLD, on_chunk=lambda _c: None)
        assert res.model_used == "opus"
        assert m["stream_opus_with_tools"].called
        assert not m["call_opus_with_tools"].called

    def test_tool_entities_survive_on_every_tier(self):
        extracted = {"origin": "JFK", "destination": "LHR"}
        for lead, expected in ((BRONZE, "sonnet"), (GOLD, "opus")):
            with _TierHarness(entities=extracted):
                res = _gen(Intent.NEW_BOOKING, lead=lead)
            assert res.model_used == expected
            assert res.tool_entities == extracted

    def test_long_message_no_longer_downgrades(self):
        with _TierHarness() as m:
            res = _gen(Intent.BOOKING_CHANGE, lead=BRONZE, raw="x" * 2000)
        assert res.model_used == "opus"
        assert m["call_opus_with_tools"].called

    def test_empty_text_with_tool_entities_falls_back_on_same_tier(self):
        with _TierHarness(text="", entities={"origin": "JFK"}) as m:
            with patch.object(generator, "call_opus", return_value=("recovered", 0.02)):
                res = _gen(Intent.NEW_BOOKING, lead=GOLD)
        assert res.text == "recovered"
        assert res.model_used == "opus"
        assert m["call_opus_with_tools"].called


class TestFallbackSiteRouting:
    """Site 2 — the step-5 path, reached when no template matches."""

    def test_second_site_uses_the_same_selection(self):
        with _TierHarness() as m:
            with patch.object(generator, "get_template", return_value=None):
                res = _gen(Intent.CLOSING, lead=GOLD)
        assert res.model_used == "opus"
        assert m["call_opus_with_tools"].called

    def test_second_site_defaults_to_sonnet(self):
        with _TierHarness() as m:
            with patch.object(generator, "get_template", return_value=None):
                res = _gen(Intent.CLOSING, lead=BRONZE)
        assert res.model_used == "sonnet"
        assert m["call_sonnet_with_tools"].called


class TestUtilityTierUnchanged:
    """Classification and the cheap call stay on Haiku."""

    def test_classify_stays_haiku(self):
        with patch.object(claude, "_call_model", return_value=("greeting", 0.0)) as m:
            claude.classify_intent("hi")
        kwargs = m.call_args.kwargs
        assert kwargs["model"] == settings.claude_haiku_model
        assert kwargs["max_tokens"] == 20

    def test_call_haiku_stays_haiku(self):
        with patch.object(claude, "_call_model", return_value=("x", 0.0)) as m:
            claude.call_haiku("sys", "msg")
        assert m.call_args.kwargs["model"] == settings.claude_haiku_model

    def test_haiku_tool_call_keeps_its_cap(self):
        with patch.object(claude, "_call_model_with_tools", return_value=("x", 0.0, {})) as m:
            claude.call_haiku_with_tools("sys", "msg")
        assert m.call_args.args[0] == settings.claude_haiku_model
        assert m.call_args.kwargs["max_tokens"] == claude.HAIKU_TOOL_MAX_TOKENS


class TestTokenCaps:
    def test_caps(self):
        assert claude.SONNET_MAX_TOKENS == 400
        assert claude.OPUS_MAX_TOKENS == 450

    def test_sonnet_call_uses_its_cap(self):
        with patch.object(claude, "_call_model", return_value=("x", 0.0)) as m:
            claude.call_sonnet("sys", "msg")
        assert m.call_args.kwargs["model"] == settings.claude_sonnet_model
        assert m.call_args.kwargs["max_tokens"] == 400

    def test_opus_call_uses_its_cap(self):
        with patch.object(claude, "_call_model", return_value=("x", 0.0)) as m:
            claude.call_opus("sys", "msg")
        assert m.call_args.kwargs["model"] == settings.claude_opus_model
        assert m.call_args.kwargs["max_tokens"] == 450

    def test_tool_variants_use_their_cap(self):
        with patch.object(claude, "_call_model_with_tools", return_value=("x", 0.0, {})) as m:
            claude.call_sonnet_with_tools("sys", "msg")
            assert m.call_args.kwargs["max_tokens"] == 400
            claude.call_opus_with_tools("sys", "msg")
            assert m.call_args.kwargs["max_tokens"] == 450


class TestCostRecording:
    def test_configured_models_have_rates(self):
        for model in (
            settings.claude_haiku_model,
            settings.claude_sonnet_model,
            settings.claude_opus_model,
        ):
            assert model in claude.COSTS, f"no cost rates for {model}"

    def test_sonnet_rate(self):
        assert claude._estimate_cost("claude-sonnet-4-6", 1000, 1000) == 0.018

    def test_opus_rate(self):
        assert claude._estimate_cost("claude-opus-4-8", 1000, 1000) == 0.03

    def test_env_overridden_id_falls_back_to_family_rate(self):
        assert claude._estimate_cost("claude-opus-4-8-20260101", 1_000_000, 0) == 5.0

    def test_unknown_family_records_zero(self):
        assert claude._estimate_cost("some-other-model", 1_000_000, 1_000_000) == 0.0
