"""Voice coherence gate — rules and content must never diverge again.

The lesson from live conversations #1193/#1194: the model imitates examples
over rules. A banned phrase surviving in a few-shot exemplar or a template
beats every "never say X" instruction. This suite renders the FULL sales
prompt and loads every live sales template, and fails the build if the old
voice reappears anywhere the client can hear it.
"""

import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import pytest

from app.ai.prompts import build_conversational_prompt
from app.ai.templates import TEMPLATES
from app.models.chat import VisitorInfo

# The old-voice filler the strategy bans. Case-insensitive.
BANNED_PHRASES = (
    "great route",
    "great choice",
    "great time",
    "solid route",
    "solid corridor",
    "solid long-haul",
    "perfect choice",
    "excellent choice",
    "wonderful choice",
)

# A banned phrase quoted as a NEGATIVE example ("never say 'great choice'")
# is curriculum, not drift — allowed only when a negation marker sits close
# before it. Templates get no such allowance: they are live client text.
_NEGATION_MARKERS = ("never", "not ", "banned", "no sycophancy", "anti-pattern")
_NEGATION_WINDOW = 150


def _render_sales_prompt(**kwargs) -> str:
    """Realistic sales render: named visitor, paid-search arrival with a
    keyword and a country landing page — exercises SITE CONTEXT + frames."""
    static, dynamic = build_conversational_prompt(
        tunnel="sales",
        visitor=VisitorInfo(name="Alex"),
        metadata={
            "utm_source": "google",
            "utm_medium": "cpc",
            "utm_term": "discount business class to sydney",
            "page_url": "https://buybusinessclass.com/flight/country/australia",
        },
        **kwargs,
    )
    return f"{static}\n{dynamic}"


def _sales_template_strings() -> list[tuple[str, str]]:
    return [
        (key, text)
        for key, variants in TEMPLATES.items()
        if ":sales" in key
        for text in variants
    ]


class TestNoBannedPhrases:
    """The banned filler is gone from everything the model learns from."""

    @pytest.mark.parametrize("phrase", BANNED_PHRASES)
    def test_prompt_free_of_banned_phrase(self, phrase):
        prompt = _render_sales_prompt()
        lowered = prompt.lower()
        for match in re.finditer(re.escape(phrase), lowered):
            window = lowered[max(0, match.start() - _NEGATION_WINDOW):match.start()]
            assert any(marker in window for marker in _NEGATION_MARKERS), (
                f"'{phrase}' appears in the sales prompt outside a negative "
                f"example (…{prompt[max(0, match.start() - 60):match.end() + 40]}…)"
            )

    @pytest.mark.parametrize("phrase", BANNED_PHRASES)
    def test_sales_templates_free_of_banned_phrase(self, phrase):
        for key, text in _sales_template_strings():
            assert phrase not in text.lower(), f"'{phrase}' survives in template {key}"


class TestOldContentGone:
    def test_value_insight_rule_deleted(self):
        # The old "insight every message" pressure contradicted NO SYCOPHANCY
        # and produced filler every message. It must never come back.
        assert "VALUE INSIGHT to every response" not in _render_sales_prompt()

    def test_route_card_has_no_price_range(self):
        for text in TEMPLATES["route_card_response:sales"]:
            assert "{price_range}" not in text, (
                "route_card_response quotes price ranges — the mechanism line "
                "replaced them (the consultant quotes numbers on the call)"
            )


class TestNewCurriculumPresent:
    """The client-first exemplar and the four frame signatures reach the model."""

    @pytest.mark.parametrize(
        "phrase",
        [
            # The most frequent first message finally has an exemplar
            "Hello, I'm looking for business class flights.",
            "You're in the right place",
            # Frame signatures — one per traveler type
            "you just did",              # VALUE_DRIVEN — the discovery
            "Consider this handled",     # TIME_IS_MONEY — the delegation
            "you're doing this right",   # EXPERIENCE_SEEKER — the milestone
            "nothing lands on you",      # NEEDS_BASED — the shared burden
            "THE BRAGGING TEST",
        ],
    )
    def test_phrase_present(self, phrase):
        assert phrase in _render_sales_prompt()

    def test_support_templates_untouched(self):
        # Ownership guard: this PR never touches the support tunnel.
        assert TEMPLATES["welcome:support"] == [
            "Hi{name_suffix}! I can help with booking changes, cancellations, "
            "or questions about your trip.",
        ]
