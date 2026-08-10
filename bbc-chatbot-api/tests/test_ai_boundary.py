"""AI boundary hardening — provider fallback + typed tool extraction.

C1: on a non-timeout API error (overloaded/rate-limit) the call retries ONCE
against settings.fallback_model — the collection never dies with the primary.
C2: tool arguments pass through ExtractedEntities (extra="forbid") — the
phantom-date and partial-json classes die at the source; one correction
re-prompt, then invalid fields are dropped, the reply stays alive.
"""

import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import anthropic
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.ai.claude import AI_FALLBACK_HEALTH, _call_model, _call_model_with_tools
from app.models.extraction import (
    ExtractedEntities,
    salvage_tool_entities,
    validate_tool_entities,
)
from config.settings import settings


class _Overloaded(anthropic.APIError):
    def __init__(self):  # noqa: D401 — anthropic's __init__ needs request objects
        Exception.__init__(self, "overloaded_error")
        self.status_code = 529


def _text_response(text="Hello!", in_tokens=10, out_tokens=5):
    resp = MagicMock()
    resp.usage.input_tokens = in_tokens
    resp.usage.output_tokens = out_tokens
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp.content = [block]
    return resp


def _tool_response(text, entities):
    resp = MagicMock()
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 5
    tblock = MagicMock()
    tblock.type = "text"
    tblock.text = text
    ublock = MagicMock()
    ublock.type = "tool_use"
    ublock.name = "save_travel_details"
    ublock.input = entities
    resp.content = [tblock, ublock]
    return resp


# ── C1: provider fallback ────────────────────────────────────────────────

class TestProviderFallback:
    def test_fallback_activates_on_overloaded(self):
        before = AI_FALLBACK_HEALTH["activations_since_boot"]
        client = MagicMock()
        client.messages.create.side_effect = [_Overloaded(), _text_response("saved by haiku")]
        with patch("app.ai.claude._get_client", return_value=client):
            text, cost = _call_model(
                settings.claude_sonnet_model, "sys", "hi", max_tokens=100, temperature=0.5,
            )
        assert text == "saved by haiku"
        assert client.messages.create.call_count == 2
        assert client.messages.create.call_args_list[1].kwargs["model"] == settings.fallback_model
        assert AI_FALLBACK_HEALTH["activations_since_boot"] == before + 1
        assert AI_FALLBACK_HEALTH["last_at"] is not None

    def test_no_fallback_on_clean_call(self):
        before = AI_FALLBACK_HEALTH["activations_since_boot"]
        client = MagicMock()
        client.messages.create.return_value = _text_response("fine")
        with patch("app.ai.claude._get_client", return_value=client):
            text, _ = _call_model(
                settings.claude_sonnet_model, "sys", "hi", max_tokens=100, temperature=0.5,
            )
        assert text == "fine"
        assert client.messages.create.call_count == 1
        assert AI_FALLBACK_HEALTH["activations_since_boot"] == before

    def test_no_second_fallback_when_fallback_model_itself_fails(self):
        client = MagicMock()
        client.messages.create.side_effect = [_Overloaded(), _Overloaded()]
        with patch("app.ai.claude._get_client", return_value=client):
            text, cost = _call_model(
                settings.claude_sonnet_model, "sys", "hi", max_tokens=100, temperature=0.5,
            )
        assert text is None
        assert client.messages.create.call_count == 2  # primary + one fallback, never more

    def test_tool_path_falls_back_too(self):
        client = MagicMock()
        client.messages.create.side_effect = [
            _Overloaded(),
            _tool_response("ok", {"origin": "JFK", "destination": "LHR"}),
        ]
        with patch("app.ai.claude._get_client", return_value=client):
            text, _cost, entities = _call_model_with_tools(
                settings.claude_sonnet_model, "sys", "jfk to lhr", max_tokens=100, temperature=0.5,
            )
        assert text == "ok"
        assert entities == {"origin": "JFK", "destination": "LHR"}
        assert client.messages.create.call_args_list[1].kwargs["model"] == settings.fallback_model

    @pytest.mark.asyncio
    async def test_health_surfaces_ai_fallback(self):
        from app.api.health import health

        with patch("app.api.health.get_scheduler_health", return_value={}), \
             patch("app.api.health.supervisor_columns_status", return_value=True), \
             patch("app.api.health.settings") as s:
            s.debug = False
            payload = await health()
        assert "ai_fallback" in payload
        assert "activations_since_boot" in payload["ai_fallback"]


# ── C2: schema truth table ───────────────────────────────────────────────

def _future(days):
    return (date.today() + timedelta(days=days)).isoformat()


class TestExtractionSchema:
    def test_valid_payload_passes(self):
        clean, err = validate_tool_entities({
            "origin": "JFK", "destination": "LHR",
            "departure_date": _future(30), "return_date": _future(37),
            "trip_type": "round_trip",
        })
        assert err is None
        assert clean["origin"] == "JFK"

    def test_extra_key_rejected(self):
        _, err = validate_tool_entities({"origin": "JFK", "hacker_field": "x"})
        assert err and "hacker_field" in err

    def test_24_7_idiom_rejected(self):
        _, err = validate_tool_entities({"departure_date": "24/7"})
        assert err and "idiom" in err

    def test_non_iso_date_rejected(self):
        _, err = validate_tool_entities({"departure_date": "July 24"})
        assert err

    def test_date_window_enforced(self):
        _, err = validate_tool_entities({"departure_date": _future(800)})
        assert err and "outside" in err
        past = (date.today() - timedelta(days=30)).isoformat()
        _, err2 = validate_tool_entities({"departure_date": past})
        assert err2

    def test_yesterday_allowed(self):
        # [today-1d, …] — timezone skew must not reject a real booking.
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        _, err = validate_tool_entities({"departure_date": yesterday})
        assert err is None

    def test_passengers_bounds(self):
        assert validate_tool_entities({"passengers": 5})[1] is None
        assert validate_tool_entities({"passengers": 0})[1]
        assert validate_tool_entities({"passengers": 12})[1]

    def test_return_before_departure_rejected(self):
        _, err = validate_tool_entities({
            "departure_date": _future(30), "return_date": _future(10),
        })
        assert err and "before departure" in err

    def test_iata_shape_enforced_and_normalized(self):
        clean, err = validate_tool_entities({"origin": "jfk"})
        assert err is None and clean["origin"] == "JFK"
        assert validate_tool_entities({"origin": "New York"})[1]

    def test_config_dict_discipline(self):
        assert ExtractedEntities.model_config.get("extra") == "forbid"

    def test_salvage_keeps_valid_drops_invalid(self):
        out = salvage_tool_entities({
            "origin": "JFK",
            "departure_date": "24/7",       # invalid → dropped
            "passengers": 2,
            "mystery": "field",             # unknown → dropped
        })
        assert out == {"origin": "JFK", "passengers": 2}


# ── C2: re-prompt path ───────────────────────────────────────────────────

class TestReprompt:
    def test_first_invalid_second_valid_is_used(self):
        client = MagicMock()
        client.messages.create.side_effect = [
            _tool_response("reply", {"origin": "JFK", "departure_date": "24/7"}),
            _tool_response("corrected", {"origin": "JFK", "departure_date": _future(20)}),
        ]
        with patch("app.ai.claude._get_client", return_value=client):
            text, _cost, entities = _call_model_with_tools(
                settings.claude_sonnet_model, "sys", "available 24/7", max_tokens=100, temperature=0.5,
            )
        assert text == "reply"  # the client-facing reply is the FIRST one
        assert entities["departure_date"] == _future(20)
        # The re-prompt carried the validation error text.
        retry_content = client.messages.create.call_args_list[1].kwargs["messages"][0]["content"]
        assert "failed validation" in retry_content

    def test_both_invalid_drops_fields_reply_alive(self):
        client = MagicMock()
        client.messages.create.side_effect = [
            _tool_response("reply", {"origin": "JFK", "departure_date": "24/7"}),
            _tool_response("still bad", {"departure_date": "24/7"}),
        ]
        with patch("app.ai.claude._get_client", return_value=client):
            text, _cost, entities = _call_model_with_tools(
                settings.claude_sonnet_model, "sys", "available 24/7", max_tokens=100, temperature=0.5,
            )
        assert text == "reply"                      # reply never crashes
        assert entities == {"origin": "JFK"}        # invalid field dropped

    def test_valid_args_never_reprompt(self):
        client = MagicMock()
        client.messages.create.return_value = _tool_response(
            "reply", {"origin": "JFK", "destination": "LHR"}
        )
        with patch("app.ai.claude._get_client", return_value=client):
            _text, _cost, entities = _call_model_with_tools(
                settings.claude_sonnet_model, "sys", "jfk lhr", max_tokens=100, temperature=0.5,
            )
        assert client.messages.create.call_count == 1
        assert entities == {"origin": "JFK", "destination": "LHR"}
