"""How the chat asks for contact details (16 Nov 2026 analysis of 3,255 chats).

What the data said, and what each test below holds the code to:
- the clients who said no were refusing CALLS, not contact ("text only",
  "email me the quotes", "I'll chat, don't want to talk"): the chat lets them
  choose, and the consultant reads the choice before dialling;
- asking again for a detail on file lost 13% of the clients on the spot, and
  the summary-loop line asked for the number all 36 times it was on file;
- 183 of the 305 "within 30 minutes" promises were said at night or on a
  weekend, US Eastern: a time is promised only while the team is online.
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.chat import VisitorInfo
from app.pipeline.intent import Intent

NOON_EST = datetime(2026, 11, 12, 17, 0, tzinfo=timezone.utc)   # Thu 12:00 US Eastern
NIGHT_EST = datetime(2026, 11, 13, 4, 0, tzinfo=timezone.utc)   # Thu 23:00 US Eastern


# ── The summary loop: a person takes over, nothing is asked again ──────────

class TestSummaryLoopLine:
    def _lines(self):
        from app.ai.templates import TEMPLATES

        return TEMPLATES["summary_loop_consultant:sales"]

    def test_it_never_asks_for_the_number_it_has(self):
        """Step 7.5 reaches it only when nothing is missing: the phone is on
        file every time, so the line asks nothing."""
        for line in self._lines():
            assert "?" not in line
            assert "number" not in line.lower()

    def test_it_promises_no_time(self):
        for line in self._lines():
            assert "minute" not in line.lower()

    def test_the_owner_dashboard_still_reads_it_as_a_person_taking_over(self):
        from app.services.owner_dashboard import classify_last_ai

        for line in self._lines():
            assert classify_last_ai(line) == "specialist"

    def test_it_is_only_reached_with_nothing_missing(self):
        """The guarantee the line relies on: the loop sits inside the branch
        where get_missing_fields (phone included) came back empty."""
        import inspect

        from app.pipeline import orchestrator

        src = inspect.getsource(orchestrator)
        branch = src.index("    if not _gmf_missing:")
        loop = src.index('"summary_loop_consultant" if _loop == "consultant"')
        assert branch < loop
        between = src[branch:loop]
        # nothing between the two lines leaves the branch at its own depth
        assert "\n    else:" not in between and "\n    elif" not in between


# ── The handoff template: the time it promises is the team's ──────────────

def _complete_lead() -> dict:
    return {
        "origin_code": "JFK", "destination_code": "LHR",
        "departure_date": "2027-03-15", "return_date": "2027-03-29", "trip_type": "round_trip",
        "passengers": 1, "route_display": "JFK → LHR",
    }


def _visitor() -> VisitorInfo:
    return VisitorInfo(name="Ann", email="ann@example.com", phone="+12125550187")


def _gen(history=None, lead=None, entities=None):
    from app.pipeline.generator import generate_response

    # budget_remaining=0: the templates, never a live model call. Left at None,
    # every call here went to the Anthropic API first (401 with the CI key) and
    # reached the template only because that call failed (CodeRabbit #255).
    return generate_response(
        intent=Intent.NEW_BOOKING, entities={"_raw_message": "ok", **(entities or {})}, kb_results=[],
        visitor=_visitor(), lead=lead or _complete_lead(), history=history or [], tunnel="sales",
        budget_remaining=0,
    )


class TestHandoffPromise:
    def test_while_the_team_is_online_it_says_thirty_minutes(self):
        with patch("app.services.closing.is_within_team_hours", return_value=True):
            res = _gen()
        assert res.model_used == "template"
        assert "~30 minutes" in res.text

    def test_at_night_it_says_first_thing(self):
        with patch("app.services.closing.is_within_team_hours", return_value=False):
            res = _gen()
        assert res.model_used == "template"
        assert "first thing" in res.text
        assert "30 minutes" not in res.text

    @pytest.mark.parametrize("online", [True, False])
    def test_the_same_promise_is_never_made_twice(self, online):
        """The guard looked for "specialist" and the line says "consultant":
        one chat got the same promise five times."""
        with patch("app.services.closing.is_within_team_hours", return_value=online):
            first = _gen().text
            again = _gen(history=[
                {"role": "user", "content": "yes"},
                {"role": "ai", "content": first},
                {"role": "user", "content": "thanks"},
            ])
        assert again.text != first

    def test_no_model_is_called_for_the_handoff(self):
        with patch("app.pipeline.generator._run_tiered_generation") as model, \
             patch("app.services.closing.is_within_team_hours", return_value=False):
            _gen()
        model.assert_not_called()


class TestHandoffContact:
    """"We'll contact you at …" names the channel the client asked for
    (CodeRabbit #255): it took the phone even after "email me the quotes"."""

    @staticmethod
    def _with(channel):
        return {**_complete_lead(), "intent_signals": {"contact_channel": channel}}

    @pytest.mark.parametrize("online", [True, False])
    @pytest.mark.parametrize("channel", ["email", "email, no calls", "no calls, no texts"])
    def test_an_email_client_is_promised_an_email(self, online, channel):
        with patch("app.services.closing.is_within_team_hours", return_value=online):
            text = _gen(lead=self._with(channel)).text
        assert "We'll contact you at ann@example.com." in text
        assert "+12125550187" not in text

    @pytest.mark.parametrize("channel", [None, "text", "no calls", "no texts", "text, email"])
    def test_otherwise_the_number_as_before(self, channel):
        lead = self._with(channel) if channel else _complete_lead()
        with patch("app.services.closing.is_within_team_hours", return_value=False):
            text = _gen(lead=lead).text
        assert "We'll contact you at +12125550187." in text

    def test_this_turns_wish_counts_before_the_lead_has_it(self):
        with patch("app.services.closing.is_within_team_hours", return_value=False):
            text = _gen(entities={"contact_channel": "email"}).text
        assert "We'll contact you at ann@example.com." in text

    def test_legacy_list_signals_keep_the_default(self):
        lead = {**_complete_lead(), "intent_signals": ["legacy_marker"]}
        with patch("app.services.closing.is_within_team_hours", return_value=False):
            text = _gen(lead=lead).text
        assert "We'll contact you at +12125550187." in text


# ── The Team line every time promise follows ───────────────────────────────

class TestTeamLine:
    def test_online_at_noon_on_a_weekday(self):
        from app.ai.prompts import TEAM_ONLINE_LINE, team_line

        assert team_line(NOON_EST) == TEAM_ONLINE_LINE

    def test_offline_at_night(self):
        from app.ai.prompts import TEAM_OFFLINE_LINE, team_line

        line = team_line(NIGHT_EST)
        assert line == TEAM_OFFLINE_LINE
        assert 'never "within 30 minutes"' in line

    def test_unknown_hours_mean_offline(self):
        from app.ai.prompts import TEAM_OFFLINE_LINE, team_line

        with patch("app.services.closing.is_within_team_hours", side_effect=RuntimeError("no clock")):
            assert team_line() == TEAM_OFFLINE_LINE

    def test_the_sales_chat_carries_it_and_support_does_not(self):
        from app.ai.prompts import build_conversational_prompt

        _s, dynamic = build_conversational_prompt(tunnel="sales", visitor=VisitorInfo())
        assert "\nTeam: o" in dynamic
        _s, dynamic = build_conversational_prompt(tunnel="support", visitor=VisitorInfo())
        assert "\nTeam: " not in dynamic

    def test_the_static_promises_point_at_it(self):
        from app.ai.prompts import build_conversational_prompt

        static, _d = build_conversational_prompt(tunnel="sales", visitor=VisitorInfo())
        assert "The time follows the Team line" in static          # CALL PRIMING
        assert "the Team line in VISITOR CONTEXT says which" in static  # HOW WE WORK
        assert "your consultant calls within ~30 minutes" not in static


# ── How the prompt asks ──────────────────────────────────────────────────────

class TestContactBlock:
    @pytest.fixture()
    def static(self):
        from app.ai.prompts import build_conversational_prompt

        static, _d = build_conversational_prompt(tunnel="sales", visitor=VisitorInfo())
        return static

    @pytest.mark.parametrize("phrase", [
        "CONTACT — ask only for what VISITOR CONTEXT does not show as provided",
        "Ask for the email as soon as the route is known",
        "in the order \"Still needed\" shows: email, then name, then the number",
        "would a text or a quick call suit you better?",
        "A second no is final",
        "NEVER ask for, or ask to confirm, a detail already provided",
        "How they want to be reached",
    ])
    def test_renders(self, static, phrase):
        assert phrase in static

    @pytest.mark.parametrize("gone", [
        "could you share your email and phone number",
        "Best number and email",
        "email and phone?",
        "Anti-Spam",
        "Sometimes emails with fare quotes go to spam",
    ])
    def test_no_double_ask_and_no_pressure_left(self, static, gone):
        assert gone not in static

    def test_the_methodology_signpost_stays(self, static):
        assert "Two quick things so your consultant can reach you" in static


# ── How the client wants to be reached, for the consultant ──────────────────

class TestContactChannel:
    @pytest.mark.parametrize("text, channel", [
        ("text only", "text"),
        ("You will have to text me my phone doesn't work down here", "text"),
        ("I am sorry, no talking at this time. Only text message chats", "text, no calls"),
        ("Okay, please email me.", "email"),
        ("Please send me some quotes on my email", "email"),
        ("you can just email me the reasonable quotes on the fare", "email"),
        ("I'll chat. Don't want to talk.", "no calls"),
        ("no phone calls", "no calls"),
        ("Don't call. I'm on the road traveling.", "no calls"),
        ("JFK to London in March, 2 adults", None),
        ("call me after 2", None),
        ("", None),
        # a negated wish is no wish; a withdrawn channel is a wish of its own
        ("Please don't text me", "no texts"),
        ("do not email me, text only", "text, no email"),
        ("not only text, a call is fine too", None),
        ("Please don't call or text me", "no calls, no texts"),
        ("don't text or call me", "no calls, no texts"),
        ("stop texting me", "no texts"),
        ("never email me", "no email"),
        ("don't text me, email me instead", "email, no texts"),
        ("I don't text much", None),
    ])
    def test_read_from_the_words(self, text, channel):
        from app.pipeline.entity_extractor import contact_channel_of

        assert contact_channel_of(text) == channel

    @pytest.mark.parametrize("stored, new, merged", [
        (None, "text", "text"),
        # CodeRabbit #255: "text only", then "please don't text me" kept "text"
        ("text", "no texts", "no texts"),
        ("no texts", "text", "text"),
        ("no calls", "no texts", "no calls, no texts"),
        ("text, no calls", "email", "text, no calls, email"),
        ("email", "no email, text", "no email, text"),
        ("text", "text", "text"),
    ])
    def test_a_later_wish_replaces_its_opposite_and_keeps_the_rest(self, stored, new, merged):
        from app.pipeline.entity_extractor import merge_contact_channel

        assert merge_contact_channel(stored, new) == merged

    @pytest.mark.asyncio
    async def test_the_lead_drops_a_withdrawn_channel(self):
        """The merge in update_lead_from_entities, not only the helper: the
        consultant's line reads "no texts", never "text" any more."""
        from unittest.mock import MagicMock

        from app.services.lead_service import update_lead_from_entities

        lead_row = {
            "id": "L1", "score": 0, "origin_code": None, "destination_code": None,
            "departure_date": None, "passengers": None, "cabin_class": None,
            "return_date": None, "trip_type": None, "children_count": None,
            "infant_count": None, "notes": "",
            "intent_signals": {"contact_channel": "text, no calls", "occasion": "business"},
        }
        leads, convs = MagicMock(), MagicMock()
        for t, data in ((leads, [lead_row]), (convs, [])):
            for m in ("select", "eq", "limit", "update", "insert"):
                getattr(t, m).return_value = t
            t.execute.return_value = MagicMock(data=data)
        client = MagicMock()
        client.table.side_effect = lambda name: {"leads": leads, "conversations": convs}[name]

        async def _sync(fn, *a, **k):
            return fn()

        with patch("app.db.supabase.get_client", return_value=client), \
             patch("app.db.supabase._run_sync", side_effect=_sync):
            await update_lead_from_entities("conv-1", {"contact_channel": "no texts"})

        written = [c.args[0] for c in leads.update.call_args_list if "intent_signals" in c.args[0]]
        assert written, "the withdrawal must be written"
        assert written[0]["intent_signals"]["contact_channel"] == "no calls, no texts"
        assert written[0]["intent_signals"]["occasion"] == "business"
        assert "Reach by: no calls, no texts" in written[0]["notes"]

    def test_extracted_with_the_rest(self):
        from app.pipeline.entity_extractor import extract_entities

        assert extract_entities("text only please, JFK to London").contact_channel == "text"

    def test_kept_as_a_signal_and_shown_to_the_consultant(self):
        from app.services.lead_service import build_chat_context_line, signals_from_entities

        signals = signals_from_entities({"contact_channel": "text, no calls", "occasion": "business"})
        assert signals["contact_channel"] == "text, no calls"
        assert "Reach by: text, no calls" in build_chat_context_line(signals)

    def test_the_orchestrator_hands_it_on(self):
        import inspect

        from app.pipeline import orchestrator

        assert '"contact_channel": extracted.contact_channel' in inspect.getsource(orchestrator)
