"""PR-9 — moderation fires only under MODERATION_V2, without the ambiguous words."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.services.moderation import detect_bad_words


@pytest.fixture(autouse=True)
def _clean():
    flags._reset_for_tests()
    yield
    flags._reset_for_tests()


def test_flag_off_matches_nothing_like_today(monkeypatch):
    # The old double-backslash pattern never matched — preserved for flag-off.
    # Clear the env too: a MODERATION_V2=true in the runner's shell must not
    # turn this into the flag-on test.
    monkeypatch.delenv("MODERATION_V2", raising=False)
    assert detect_bad_words("you idiot") is None
    assert detect_bad_words("this is a scam") is None


def test_flag_on_detects_real_abuse(monkeypatch):
    monkeypatch.setenv("MODERATION_V2", "true")
    assert detect_bad_words("you idiot") == "idiot"
    assert detect_bad_words("fuck this") == "fuck"


def test_flag_on_ignores_ambiguous_complaint_words(monkeypatch):
    monkeypatch.setenv("MODERATION_V2", "true")
    for phrase in ("this is a scam", "what a fraud", "that's fake", "you liar", "this is stupid", "feels like a cheat"):
        assert detect_bad_words(phrase) is None, phrase


def test_word_boundary(monkeypatch):
    monkeypatch.setenv("MODERATION_V2", "true")
    # "crapshoot" must not trip "crap"
    assert detect_bad_words("it's a crapshoot") is None
