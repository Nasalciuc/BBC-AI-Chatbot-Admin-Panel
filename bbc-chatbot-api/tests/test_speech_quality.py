"""Speech quality — rhythm over straitjacket, live sales greeting, warmer temp."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from unittest.mock import patch

import pytest

from app.ai import claude
from app.ai.prompts import build_conversational_prompt
from app.models.chat import VisitorInfo
from app.pipeline.generator import generate_response
from app.pipeline.intent import Intent


class TestRhythmPrompt:
    def test_rhythm_and_no_sycophancy_present(self):
        static, _ = build_conversational_prompt(tunnel="sales", visitor=VisitorInfo())
        assert "RHYTHM: default 2-3 SHORT sentences" in static
        assert "NO SYCOPHANCY" in static
        assert "flexibility probe after a date" in static

    def test_old_sentence_caps_gone(self):
        static, _ = build_conversational_prompt(tunnel="sales", visitor=VisitorInfo())
        assert "Maximum 2 sentences per response" not in static
        assert "Keep it to 2 sentences total" not in static


class TestSalesGreetingLive:
    def test_sales_greeting_invokes_generation_with_site_context(self):
        entities = {
            "_raw_message": "hi",
            "_metadata": {
                "utm_source": "google",
                "utm_term": "cheap business class to india",
                "page_url": "/flight/country/india/410",
            },
        }
        captured = {}

        def _fake_tiered(intent, lead, tunnel, system_parts, raw_message, on_chunk=None):
            static, dynamic = system_parts
            captured["prompt"] = static + dynamic
            captured["tunnel"] = tunnel
            captured["intent"] = intent
            return ("Looking at India — Delhi or Mumbai?", 0.01, None, "sonnet", "default")

        with patch(
            "app.pipeline.generator._run_tiered_generation", side_effect=_fake_tiered
        ):
            res = generate_response(
                intent=Intent.GREETING,
                entities=entities,
                kb_results=[],
                visitor=VisitorInfo(name="Alex"),
                lead=None,
                history=[],
                tunnel="sales",
            )

        assert res.model_used == "sonnet"
        assert "India" in res.text
        assert captured["tunnel"] == "sales"
        assert captured["intent"] == Intent.GREETING
        assert "[SITE CONTEXT]" in captured["prompt"] or "google" in captured["prompt"].lower()

    def test_sales_greeting_falls_back_to_template_on_failure(self):
        empty = (None, 0.0, {})
        with (
            patch("app.pipeline.generator.call_sonnet_with_tools", return_value=empty),
            patch("app.pipeline.generator.call_opus_with_tools", return_value=empty),
            patch("app.pipeline.generator.stream_sonnet_with_tools", return_value=empty),
            patch("app.pipeline.generator.stream_opus_with_tools", return_value=empty),
            patch("app.pipeline.generator.call_haiku_with_tools", return_value=empty),
            patch("app.pipeline.generator.stream_haiku_with_tools", return_value=empty),
        ):
            res = generate_response(
                intent=Intent.GREETING,
                entities={"_raw_message": "hi"},
                kb_results=[],
                visitor=VisitorInfo(),
                lead=None,
                history=[],
                tunnel="sales",
            )
        assert res.model_used == "template"
        assert res.text
        assert "welcome" in res.text.lower() or "hi" in res.text.lower()

    def test_support_greeting_stays_template(self):
        with patch("app.pipeline.generator._run_tiered_generation") as tiered:
            res = generate_response(
                intent=Intent.GREETING,
                entities={"_raw_message": "hi"},
                kb_results=[],
                visitor=VisitorInfo(),
                lead=None,
                history=[],
                tunnel="support",
            )
        assert not tiered.called
        assert res.model_used == "template"
        assert res.text


class TestGenerationTemperature:
    def test_sonnet_and_opus_gen_use_0_6(self):
        with patch.object(claude, "_call_model", return_value=("x", 0.0)) as m:
            claude.call_sonnet("sys", "msg")
            assert m.call_args.kwargs["temperature"] == 0.6
            claude.call_opus("sys", "msg")
            assert m.call_args.kwargs["temperature"] == 0.6

    def test_tool_variants_use_0_6(self):
        with patch.object(
            claude, "_call_model_with_tools", return_value=("x", 0.0, {})
        ) as m:
            claude.call_sonnet_with_tools("sys", "msg")
            assert m.call_args.kwargs["temperature"] == 0.6
            claude.call_opus_with_tools("sys", "msg")
            assert m.call_args.kwargs["temperature"] == 0.6

    def test_stream_variants_use_0_6(self):
        with patch.object(
            claude, "_stream_model_with_tools", return_value=("x", 0.0, {})
        ) as m:
            claude.stream_sonnet_with_tools("sys", "msg")
            assert m.call_args.kwargs["temperature"] == 0.6
            claude.stream_opus_with_tools("sys", "msg")
            assert m.call_args.kwargs["temperature"] == 0.6
        with patch.object(claude, "_stream_model", return_value=("x", 0.0)) as m:
            claude.stream_sonnet("sys", "msg")
            assert m.call_args.kwargs["temperature"] == 0.6
            claude.stream_opus("sys", "msg")
            assert m.call_args.kwargs["temperature"] == 0.6

    def test_classification_and_learning_temps_unchanged(self):
        with patch.object(claude, "_call_model", return_value=("x", 0.0)) as m:
            claude.call_haiku("sys", "msg")
            assert m.call_args.kwargs["temperature"] == 0.3
            claude.call_sonnet_learning("sys", "msg")
            assert m.call_args.kwargs["temperature"] == 0.2
            claude.classify_intent("hello")
            assert m.call_args.kwargs["temperature"] == 0
        with patch.object(
            claude, "_call_model_with_tools", return_value=("x", 0.0, {})
        ) as m:
            claude.call_haiku_with_tools("sys", "msg")
            assert m.call_args.kwargs["temperature"] == 0.3
