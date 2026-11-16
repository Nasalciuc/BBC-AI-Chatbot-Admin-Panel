"""REPLY_MODEL=haiku55 — the owner's cost switch (16 Nov: "Haiku 5.5 as the base,
or that volume bankrupts us"). Every customer reply, the closing included, goes to
Haiku 5.5; DECISIVE_MODEL=sonnet55 lifts the decisive turns to Sonnet 5.5. Unset,
every route is today's."""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.ai import router
from app.ai.prices import cost_from_usage
from app.pipeline.intent import Intent
from config.settings import settings


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("REPLY_MODEL", raising=False)
    monkeypatch.delenv("DECISIVE_MODEL", raising=False)
    monkeypatch.delenv("REPLY_EFFORT", raising=False)
    flags._reset_for_tests()
    yield
    flags._reset_for_tests()


def _set(monkeypatch, **env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    flags._reset_for_tests()


def test_unset_is_today():
    assert router.owner_reply_model(decisive=False) is None
    assert router.owner_reply_model(decisive=True) is None


def test_haiku55_answers_every_turn(monkeypatch):
    _set(monkeypatch, REPLY_MODEL="haiku55")
    assert settings.claude_haiku_55_model == "claude-haiku-5-5"
    assert router.owner_reply_model(decisive=False) == "claude-haiku-5-5"
    assert router.owner_reply_model(decisive=True) == "claude-haiku-5-5"


def test_decisive_turns_can_step_up_to_sonnet55(monkeypatch):
    _set(monkeypatch, REPLY_MODEL="haiku55", DECISIVE_MODEL="sonnet55")
    assert router.owner_reply_model(decisive=False) == "claude-haiku-5-5"
    assert router.owner_reply_model(decisive=True) == settings.claude_sonnet_55_model == "claude-sonnet-5-5"


def test_decisive_model_alone_changes_nothing(monkeypatch):
    _set(monkeypatch, DECISIVE_MODEL="sonnet55")
    assert router.owner_reply_model(decisive=True) is None


def test_an_unknown_value_is_today(monkeypatch):
    _set(monkeypatch, REPLY_MODEL="cheapest")
    assert router.owner_reply_model(decisive=False) is None
    assert router.owner_reply_route(decisive=False) == {}


def test_effort_is_low_unless_the_owner_raises_it(monkeypatch):
    """Haiku 5.5 thinks inside max_tokens: a higher effort gets more room."""
    _set(monkeypatch, REPLY_MODEL="haiku55")
    assert router.owner_reply_route(decisive=False) == {"model": "claude-haiku-5-5", "effort": "low", "max_tokens": 1024}
    _set(monkeypatch, REPLY_MODEL="haiku55", REPLY_EFFORT="medium")
    assert router.owner_reply_route(decisive=False) == {"model": "claude-haiku-5-5", "effort": "medium", "max_tokens": 2048}
    _set(monkeypatch, REPLY_MODEL="haiku55", REPLY_EFFORT="high")
    assert router.owner_reply_route(decisive=False)["max_tokens"] == 4096
    _set(monkeypatch, REPLY_MODEL="haiku55", REPLY_EFFORT="max")
    assert router.owner_reply_route(decisive=False)["effort"] == "low"


def test_the_switch_wins_on_the_live_engine_below_any_budget_line(monkeypatch):
    """The legacy engine on the 4.x family, 10% of the day's budget: today the
    override is empty (Sonnet 4.6 / Opus 4.8 answer); with the switch, Haiku 5.5."""
    from app.ai import ledger
    from app.pipeline import generator

    monkeypatch.setattr(ledger, "budget_fraction", lambda: 0.1)
    monkeypatch.setattr(generator, "_family_55_active", lambda cid: False)
    assert generator._router_override("sonnet", "sales", "c1") == {}
    assert generator._router_override("opus", "sales", "c1") == {}

    _set(monkeypatch, REPLY_MODEL="haiku55")
    expected = {"model": "claude-haiku-5-5", "effort": "low", "max_tokens": 1024}
    assert generator._router_override("sonnet", "sales", "c1") == expected
    assert generator._router_override("opus", "sales", "c1") == expected
    assert generator._router_override("haiku", "support", "c1") == expected


def test_the_reply_is_written_down_under_the_model_that_answered(monkeypatch):
    from app.pipeline import generator

    seen = {}

    def tools(system, message, **kw):
        seen.update(kw)
        return "Where would you like to fly?", 0.0001, {}

    monkeypatch.setattr(generator, "_tier_calls", lambda tier: (tools, tools, None))
    monkeypatch.setattr(
        generator, "_router_override",
        lambda tier, tunnel, cid: {"model": "claude-haiku-5-5", "effort": "low", "max_tokens": 1024},
    )
    text, _cost, _ents, label, _reason = generator._run_tiered_generation(
        Intent.NEW_BOOKING, None, "sales", ("static", "dynamic"), "hi"
    )
    assert text == "Where would you like to fly?"
    assert label == "claude-haiku-5-5"
    assert seen["model"] == "claude-haiku-5-5"


def test_without_the_switch_the_label_stays_the_tier(monkeypatch):
    from app.pipeline import generator

    monkeypatch.setattr(generator, "_tier_calls", lambda tier: (lambda s, m, **kw: ("Hi!", 0.0, {}), None, None))
    monkeypatch.setattr(generator, "_router_override", lambda tier, tunnel, cid: {})
    *_, label, _reason = generator._run_tiered_generation(
        Intent.NEW_BOOKING, None, "sales", ("static", "dynamic"), "hi"
    )
    assert label == "sonnet"


def test_the_re_call_without_tools_stays_on_the_owners_model(monkeypatch):
    """A tool call that brings entities but no text is asked again without tools:
    that second call must not slip back to Sonnet 4.6."""
    from app.ai import claude
    from app.pipeline import generator

    def tier_plain(system, message):
        raise AssertionError("the 4.x plain call ran")

    calls = []
    monkeypatch.setattr(
        generator, "_tier_calls",
        lambda tier: (lambda s, m, **kw: ("", 0.0001, {"destination": "LHR"}), None, tier_plain),
    )
    monkeypatch.setattr(
        generator, "_router_override",
        lambda tier, tunnel, cid: {"model": "claude-haiku-5-5", "effort": "high", "max_tokens": 4096},
    )

    def plain(model, s, m, effort="low", max_tokens=None):
        calls.append((model, effort, max_tokens))
        return "London it is!", 0.0001

    monkeypatch.setattr(claude, "call_reply_plain", plain)
    text, *_ = generator._run_tiered_generation(
        Intent.NEW_BOOKING, None, "sales", ("static", "dynamic"), "London"
    )
    # The route's effort and room for thinking go with it (CodeRabbit #255).
    assert calls == [("claude-haiku-5-5", "high", 4096)]
    assert text == "London it is!"


def test_the_closing_follows_the_switch(monkeypatch):
    from app.ai import claude
    from app.services import closing

    used = []
    efforts = []
    room = []

    def reply(model, system, user, on_chunk=None, effort="low", max_tokens=None):
        used.append(model)
        efforts.append(effort)
        room.append(max_tokens)
        if on_chunk:
            on_chunk("Thank you")
        return "Thank you, Robert.", 0.0001

    def opus(system, user, on_chunk=None):
        used.append("opus")
        if on_chunk:
            on_chunk("Thank you")
        return "Thank you, Robert.", 0.01

    monkeypatch.setattr(claude, "stream_reply", reply)
    monkeypatch.setattr(claude, "stream_opus", opus)
    monkeypatch.setattr(closing, "get_brand_vars", lambda site: {"contact_phone": "+1 (888) 322-7999"})
    lead = {"origin_code": "JFK", "destination_code": "LHR", "departure_date": "2026-12-10"}
    visitor = SimpleNamespace(name="Robert", email="", phone="")

    _text, model, _cost = asyncio.run(closing.generate_closing(lead, visitor, crm_ok=True))
    assert used == ["opus"] and model == settings.claude_opus_model

    used.clear()
    _set(monkeypatch, REPLY_MODEL="haiku55")
    _text, model, _cost = asyncio.run(closing.generate_closing(lead, visitor, crm_ok=True))
    assert used == ["claude-haiku-5-5"] and model == "claude-haiku-5-5"

    used.clear()
    _set(monkeypatch, REPLY_MODEL="haiku55", DECISIVE_MODEL="sonnet55", REPLY_EFFORT="medium")
    _text, model, _cost = asyncio.run(closing.generate_closing(lead, visitor, crm_ok=True))
    assert used == ["claude-sonnet-5-5"] and model == "claude-sonnet-5-5"
    assert efforts == ["low", "medium"]
    # The whole route, max_tokens included (CodeRabbit #255).
    assert room == [1024, 2048]


class _FakeStream:
    """Enough of messages.stream for _stream_model: one chunk, then usage."""

    def __init__(self, text):
        self._text = text
        self.text_stream = iter([text])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._text)],
            usage=SimpleNamespace(
                input_tokens=10, output_tokens=5,
                cache_read_input_tokens=0, cache_creation_input_tokens=0, cache_creation=None,
            ),
            stop_reason="end_turn",
        )


class _FakeClient:
    def __init__(self, sent):
        self.sent = sent
        self.messages = self

    def stream(self, **kwargs):
        self.sent.append(kwargs)
        return _FakeStream("Thank you, Robert.")

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="London it is!")],
            usage=SimpleNamespace(
                input_tokens=10, output_tokens=5,
                cache_read_input_tokens=0, cache_creation_input_tokens=0, cache_creation=None,
            ),
            stop_reason="end_turn",
        )


def test_the_real_wrappers_send_the_routes_effort_and_room(monkeypatch):
    """stream_reply (the closing) and call_reply_plain (the re-call) reach the
    API with the route's effort and max_tokens, not 450 / 400 raised to 512."""
    from app.ai import claude

    sent = []
    monkeypatch.setattr(claude, "_get_client", lambda: _FakeClient(sent))

    text, _cost = claude.stream_reply("claude-haiku-5-5", "system", "user", effort="high", max_tokens=4096)
    assert text == "Thank you, Robert."
    assert sent[-1]["max_tokens"] == 4096
    assert sent[-1]["output_config"] == {"effort": "high"}

    text, _cost = claude.call_reply_plain("claude-haiku-5-5", "system", "user", effort="medium", max_tokens=2048)
    assert text == "London it is!"
    assert sent[-1]["max_tokens"] == 2048
    assert sent[-1]["extra_body"] == {"output_config": {"effort": "medium"}}

    # Without a route, today's defaults.
    claude.call_reply_plain("claude-haiku-5-5", "system", "user")
    assert sent[-1]["max_tokens"] == 512
    assert sent[-1]["extra_body"] == {"output_config": {"effort": "low"}}


def test_a_typical_reply_costs_thirty_times_less(monkeypatch):
    """The average Sonnet sales reply in ai_calls (10 calls, 2-16 Nov): 552 tokens
    in, 2,762 read from the cache, 69 out. Sonnet 4.6 vs Haiku 5.5, at the list
    prices in prices.py (thinking tokens would add to the Haiku 5.5 side)."""
    usage = SimpleNamespace(
        input_tokens=552, output_tokens=69,
        cache_read_input_tokens=2762, cache_creation_input_tokens=0, cache_creation=None,
    )
    sonnet = cost_from_usage("claude-sonnet-4-6", usage)
    haiku55 = cost_from_usage("claude-haiku-5-5", usage)
    assert sonnet == pytest.approx(0.00352, abs=1e-5)
    assert haiku55 == pytest.approx(0.000117, abs=1e-6)
    assert sonnet / haiku55 > 29
