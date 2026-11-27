"""Test 1: the card after the site's quote window was closed unsent (app/services/proactive.py, form_left; owner,
27 Nov 2026, "Testul 1: plan de implementare", step 2).

Pins: three arms stable per visitor, a third each, salted apart from every other test; FORM_LEFT_TEST off answers
"trigger_off" and /chat/live names no arm; the off arm is asked and counted, never shown; A's words and buttons; B's by
the team's hours and an agent online ("about a minute" only then; a Saturday is night); a language with no words yet
falls back to English, counted; the row asked after a card answers by the arms; what the widget says about the window
is kept to known words; and the branch not taken: every other moment answers exactly as it did.
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.api.chat import ProactiveOfferRequest, chat_live, LivePassRequest, proactive_offer
from app.experiments import bucket
from app.models.trip_context import TripContext
from app.services import card_words, proactive

_TRIP = TripContext(source="kayak", origin_code="JFK", destination_code="CDG")


def _visitor(arm: str) -> str:
    """A visitor id in `arm` ("off", "a", "b")."""
    for i in range(1000):
        vid = f"visitor-form-left-{i:04d}"
        b = bucket(proactive.FORM_LEFT_EXPERIMENT, vid)
        if (arm == "off" and b <= 33) or (arm == "a" and 34 <= b <= 66) or (arm == "b" and b >= 67):
            return vid
    raise AssertionError(arm)


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    flags._reset_for_tests()
    for k in ("PROACTIVE_ENABLED", "PROACTIVE_PERCENT", "PROACTIVE_TRIGGERS", "FORM_LEFT_TEST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PROACTIVE_ENABLED", "true")
    monkeypatch.setenv("PROACTIVE_PERCENT", "100")
    saved = {k: (dict(v) if isinstance(v, dict) else v) for k, v in proactive.PROACTIVE_HEALTH.items()}
    for k, v in proactive.PROACTIVE_HEALTH.items():
        if isinstance(v, dict):
            for kk in v:
                v[kk] = 0
        elif isinstance(v, int):
            proactive.PROACTIVE_HEALTH[k] = 0
    yield
    proactive.PROACTIVE_HEALTH.clear()
    proactive.PROACTIVE_HEALTH.update(saved)
    flags._reset_for_tests()


def _on(monkeypatch):
    monkeypatch.setenv("FORM_LEFT_TEST", "true")


# --- the arms -------------------------------------------------------------

def test_the_arms_are_stable_and_a_third_each(monkeypatch):
    _on(monkeypatch)
    ids = [f"v-{i:05d}-xyzw" for i in range(10_000)]
    arms = [proactive.form_left_arm(v) for v in ids]
    assert arms == [proactive.form_left_arm(v) for v in ids]
    for arm in ("off", "a", "b"):
        assert 0.30 < arms.count(arm) / len(ids) < 0.37, arm
    # Salted apart: the proactive chat's own buckets and the Kayak card's don't decide this arm.
    other = [bucket(proactive.EXPERIMENT, v) <= 33 for v in ids[:2000]]
    assert other != [a == "off" for a in arms[:2000]]


def test_no_arm_while_the_test_is_off_or_without_a_visitor(monkeypatch):
    assert proactive.form_left_arm(_visitor("a")) is None
    _on(monkeypatch)
    assert proactive.form_left_arm(None) is None
    assert proactive.form_left_arm("") is None


def test_the_test_off_answers_trigger_off_and_the_feature_off_answers_off(monkeypatch):
    out = proactive.offer(_visitor("a"), "form_left", _TRIP)
    assert out == {"show": False, "arm": proactive.TRIGGER_OFF, "trigger": "form_left", "message": None,
                   "quick_replies": [], "reason": "test_off"}
    _on(monkeypatch)
    monkeypatch.setenv("PROACTIVE_ENABLED", "false")
    assert proactive.offer(_visitor("a"), "form_left", _TRIP)["arm"] == "off"


def test_the_off_arm_is_asked_and_counted_never_shown(monkeypatch):
    _on(monkeypatch)
    with patch("app.db.supabase.get_client", side_effect=AssertionError("offer wrote")):
        out = proactive.offer(_visitor("off"), "form_left", _TRIP)
    assert out["show"] is False
    assert out["arm"] == proactive.FORM_LEFT_OFF
    assert out["form_left"] == "off"
    assert proactive.PROACTIVE_HEALTH["form_left_off"] == 1
    assert proactive.PROACTIVE_HEALTH["form_left_offers"] == 1
    assert proactive.PROACTIVE_HEALTH["shown"] == 0


def test_the_proactive_chats_control_sees_no_card_here_either(monkeypatch):
    _on(monkeypatch)
    monkeypatch.setenv("PROACTIVE_PERCENT", "0")
    out = proactive.offer(_visitor("a"), "form_left", _TRIP)
    assert (out["show"], out["arm"]) == (False, "control")


# --- the words ------------------------------------------------------------

def test_arm_a_asks_where_the_fares_go_without_the_form(monkeypatch):
    _on(monkeypatch)
    out = proactive.offer(_visitor("a"), "form_left", _TRIP, device="phone")
    assert out["show"] is True
    assert out["arm"] == "form_left_a"
    assert out["variant"] == proactive.FL_A
    assert out["message"].startswith("No form needed. Where should an agent send the fares for ")
    assert out["message"].endswith("? Text or email, one is enough.")
    assert out["quick_replies"] == ["Not now"]
    assert out["buttons"] == [{"kind": "sms", "label": "Text me"}, {"kind": "email", "label": "Email me"}]
    # Without a route: the same ask, without the line.
    bare = proactive.offer(_visitor("a"), "form_left", None)
    assert bare["message"] == "No form needed. Where should an agent send the fares? Text or email, one is enough."
    assert proactive.PROACTIVE_HEALTH["form_left_shown"]["fl_a"] == 2


@pytest.mark.parametrize("team, agent, variant, text, buttons", [
    (True, True, "fl_b_day", "Want to talk it through now? An agent can answer you right here in about a minute.",
     [{"kind": "wait", "label": "Yes, chat now"}]),
    (True, False, "fl_b_day_quiet", "Want to talk it through? An agent can help you right here.",
     [{"kind": "wait", "label": "Yes, chat"}]),
    (False, True, "fl_b_night", "Want the fares by text or email? Our team will follow up as soon as we're back.",
     [{"kind": "sms", "label": "Text me"}, {"kind": "email", "label": "Email me"}]),
    (False, False, "fl_b_night", "Want the fares by text or email? Our team will follow up as soon as we're back.",
     [{"kind": "sms", "label": "Text me"}, {"kind": "email", "label": "Email me"}]),
])
def test_arm_b_by_the_teams_hours_and_an_agent_online(monkeypatch, team, agent, variant, text, buttons):
    _on(monkeypatch)
    out = proactive.offer(_visitor("b"), "form_left", _TRIP, team_online=team, agent_online=agent)
    assert (out["show"], out["arm"], out["variant"]) == (True, "form_left_b", variant)
    assert out["message"] == text
    assert out["buttons"] == buttons
    # The minute is promised only with an agent online, and no hour at night.
    assert ("about a minute" in out["message"]) is (team and agent)
    assert not any(h in out["message"] for h in ("9 AM", "Eastern"))


def test_a_saturday_is_night():
    from app.services.closing import is_within_team_hours
    saturday_noon_ny = datetime(2026, 11, 28, 17, 0, tzinfo=timezone.utc)
    assert is_within_team_hours(saturday_noon_ny) is False
    assert proactive.form_left_variant("b", is_within_team_hours(saturday_noon_ny), True) == proactive.FL_B_NIGHT


def test_a_language_without_words_falls_back_to_english_counted(monkeypatch):
    _on(monkeypatch)
    out = proactive.offer(_visitor("a"), "form_left", _TRIP, lang="es")
    assert out["message"].startswith("No form needed.")
    assert "lang" not in out
    assert proactive.PROACTIVE_HEALTH["form_left_words_missing"] == 1
    # English asked: nothing missing.
    proactive.offer(_visitor("a"), "form_left", _TRIP)
    assert proactive.PROACTIVE_HEALTH["form_left_words_missing"] == 1


def test_the_chat_opens_with_the_cards_words_and_carries_the_test(monkeypatch):
    _on(monkeypatch)
    for arm in ("a", "b"):
        out = proactive.offer(_visitor(arm), "form_left", _TRIP)
        assert proactive.compose("form_left", _TRIP, None, out["variant"])[0] == out["message"]
        assert proactive.applied_variant("form_left", _TRIP, out["variant"]) == out["variant"]
        meta = proactive.init_metadata("form_left", _TRIP, out["variant"])
        assert meta["form_left_test"] == proactive.FORM_LEFT_TEST_ID
        assert meta["form_left_arm"] == arm
        assert meta["proactive_variant"] == out["variant"]
    # A variant that is not the test's is dropped: no arm on the chat.
    assert "form_left_arm" not in proactive.init_metadata("form_left", _TRIP, "beat")
    assert proactive.known_variant("fl_b_night") == "fl_b_night"


# --- the row after a card -------------------------------------------------

def test_the_row_after_a_card_answers_by_the_arms(monkeypatch):
    _on(monkeypatch)
    off = proactive.offer(_visitor("off"), "quote", _TRIP, place="form", after_card=True)
    assert (off["show"], off["arm"]) == (False, proactive.FORM_LEFT_OFF)
    row = proactive.offer(_visitor("a"), "quote", _TRIP, place="form", after_card=True)
    assert (row["show"], row["variant"], row["arm"]) == (True, proactive.ROW, "form_left_a")
    assert row["buttons"] == card_words.row_buttons(None)
    # The row as the quote moment writes it today.
    assert row["message"] == proactive.compose("quote", _TRIP, None, proactive.ROW)[0]
    # No form to stand beside: no row.
    assert proactive.offer(_visitor("b"), "quote", _TRIP, after_card=True)["reason"] == "no_row"
    assert proactive.PROACTIVE_HEALTH["form_left_shown"]["row"] == 1


def test_the_branch_not_taken_every_other_moment_answers_as_before(monkeypatch):
    """FORM_LEFT_TEST on changes nothing for kayak, ads, reader, quote and the row asked as today (no after_card)."""
    asks = [
        ("kayak", dict()), ("reader", dict()), ("quote", dict(refused=["email"])), ("quote", dict(place="form")),
        ("note", dict()),
    ]
    before = [proactive.offer(_visitor("a"), t, _TRIP, **kw) for t, kw in asks]
    _on(monkeypatch)
    after = [proactive.offer(_visitor("a"), t, _TRIP, **kw) for t, kw in asks]
    assert after == before
    assert proactive.PROACTIVE_HEALTH["form_left_offers"] == 0


# --- what the widget sends ------------------------------------------------

def test_the_window_is_kept_to_known_words_never_a_422():
    req = ProactiveOfferRequest(
        visitor_id="visitor-0123456789", trigger="form_left",
        left={"how": "close_button", "source": "dom", "focused": True, "typed": "yes", "value": "dana@example.com"},
        after_card="true",
    )
    assert req.left == {"how": "close_button", "source": "dom", "focused": True}
    assert req.after_card is False
    assert ProactiveOfferRequest(visitor_id="visitor-0123456789", trigger="form_left", left={"how": "unmounted"}).left is None
    assert ProactiveOfferRequest(visitor_id="visitor-0123456789", trigger="form_left", left="x").left is None
    assert ProactiveOfferRequest(visitor_id="visitor-0123456789", trigger="quote", after_card=True).after_card is True


@pytest.mark.asyncio
async def test_the_route_reads_an_agent_online_only_for_b_in_the_teams_hours(monkeypatch):
    _on(monkeypatch)
    agents = AsyncMock(return_value=[{"id": "x"}])
    with patch("app.services.closing.is_within_team_hours", return_value=True), \
         patch("app.db.supabase.get_available_agents", agents):
        b = await proactive_offer(ProactiveOfferRequest(
            visitor_id=_visitor("b"), trigger="form_left", left={"how": "escape"}), None)
        assert b["variant"] == proactive.FL_B_DAY
        assert agents.await_count == 1
        await proactive_offer(ProactiveOfferRequest(visitor_id=_visitor("a"), trigger="form_left"), None)
        await proactive_offer(ProactiveOfferRequest(visitor_id=_visitor("b"), trigger="kayak"), None)
        assert agents.await_count == 1
    assert proactive.PROACTIVE_HEALTH["form_left_how"]["escape"] == 1
    with patch("app.services.closing.is_within_team_hours", return_value=False), \
         patch("app.db.supabase.get_available_agents", agents):
        night = await proactive_offer(ProactiveOfferRequest(visitor_id=_visitor("b"), trigger="form_left"), None)
        assert night["variant"] == proactive.FL_B_NIGHT
        assert agents.await_count == 1


@pytest.mark.asyncio
async def test_agents_unknown_promise_no_minute_and_are_counted(monkeypatch, caplog):
    _on(monkeypatch)
    with patch("app.services.closing.is_within_team_hours", return_value=True), \
         patch("app.db.supabase.get_available_agents", AsyncMock(side_effect=RuntimeError("down"))):
        out = await proactive_offer(ProactiveOfferRequest(visitor_id=_visitor("b"), trigger="form_left"), None)
    assert out["variant"] == proactive.FL_B_DAY_QUIET
    assert proactive.PROACTIVE_HEALTH["form_left_agents_unknown"] == 1
    assert "agents online unknown" in caplog.text


def _live_request():
    req = MagicMock()
    req.headers = {}
    req.client.host = "203.0.113.7"
    return req


@pytest.mark.asyncio
@pytest.mark.parametrize("test_on", [True, False])
async def test_chat_live_names_the_arm_only_while_the_test_runs(monkeypatch, test_on):
    if test_on:
        _on(monkeypatch)
    vid = _visitor("b")
    with patch("app.services.live_visitors.widget_pass", AsyncMock(return_value={"enabled": False})), \
         patch("app.services.agent_first_test.side", return_value=None):
        res = await chat_live(LivePassRequest(visitor_id=vid), _live_request())
    import json
    body = json.loads(res.body)
    if test_on:
        assert body["form_left"] == "b"
    else:
        assert "form_left" not in body
