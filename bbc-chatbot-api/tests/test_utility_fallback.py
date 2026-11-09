"""CodeRabbit #226: the emergency fallback must reach the OLD Haiku.

With UTILITY_MODEL_55 on, a classify/iata/summary call goes to Haiku 5.5.
fallback_model is the old Haiku, and the recursive fallback call used to be
re-routed to Haiku 5.5 too: the model that had just failed was called again
and the recovery never happened."""

import os
import sys
from unittest.mock import MagicMock, patch

import anthropic
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags  # noqa: E402
from app.ai import claude  # noqa: E402
from config.settings import settings  # noqa: E402


def _ok_response():
    r = MagicMock()
    r.stop_reason = "end_turn"
    r.id = "msg_1"
    r.usage = MagicMock(input_tokens=10, output_tokens=2, cache_read_input_tokens=0,
                        cache_creation_input_tokens=0)
    block = MagicMock(); block.type = "text"; block.text = "ok"
    r.content = [block]
    return r


def _run(monkeypatch, flag_on: bool):
    flags._reset_for_tests()
    monkeypatch.setenv("UTILITY_MODEL_55", "true" if flag_on else "false")
    seen = []
    err = anthropic.APIError("overloaded", httpx.Request("POST", "https://api.anthropic.com"), body=None)

    def _create(**kw):
        seen.append(kw["model"])
        if len(seen) == 1:
            raise err
        return _ok_response()

    client = MagicMock()
    client.messages.create.side_effect = _create
    with patch.object(claude, "_get_client", return_value=client), \
         patch.object(claude, "_record_fallback"), \
         patch.object(claude, "record_ai_call"):
        text, _ = claude._call_model(settings.claude_haiku_model, "sys", "hi", 50, 0.0, task="classify")
    flags._reset_for_tests()
    return text, seen


def test_the_fallback_reaches_the_old_haiku(monkeypatch):
    text, seen = _run(monkeypatch, flag_on=True)
    assert seen == [settings.claude_haiku_55_model, settings.fallback_model]
    assert text == "ok"


def test_flag_off_keeps_the_old_haiku_and_has_no_fallback_to_itself(monkeypatch):
    text, seen = _run(monkeypatch, flag_on=False)
    # haiku == fallback_model: no second call to the same model
    assert seen == [settings.claude_haiku_model]
    assert text is None


def test_a_55_call_sends_effort_in_the_body_not_as_a_keyword(monkeypatch):
    # CodeRabbit #226: requirements allow SDKs without the typed output_config
    # parameter; there the keyword is a TypeError, and classify fell back to
    # GENERAL_QUESTION without trying the old Haiku.
    flags._reset_for_tests()
    monkeypatch.setenv("UTILITY_MODEL_55", "true")
    seen = {}

    def _create(**kw):
        seen.update(kw)
        return _ok_response()

    client = MagicMock()
    client.messages.create.side_effect = _create
    with patch.object(claude, "_get_client", return_value=client), patch.object(claude, "record_ai_call"):
        text, _ = claude._call_model(settings.claude_haiku_model, "sys", "hi", 50, 0.0, task="classify")
    flags._reset_for_tests()
    assert text == "ok" and seen["model"] == settings.claude_haiku_55_model
    assert "output_config" not in seen
    assert seen["extra_body"] == {"output_config": {"effort": "low"}}
