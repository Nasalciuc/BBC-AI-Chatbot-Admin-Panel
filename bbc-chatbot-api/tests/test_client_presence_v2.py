"""The client's presence, rebuilt (Nov 2026): one rule, read by everything.

What was wrong, measured on 581 chats of the 30 days to 12 Nov 2026:
  * the widget only spoke while the chat was OPEN, so "on the site, chat
    closed" was the last thing it had said, however long ago;
  * "left" came from a pagehide beacon that a killed tab or a phone put to
    sleep never sends: 171 chats ended "online", 263 "minimized", 124 "left";
  * the panel showed a derived state, while the "client left" sweep, the
    queue and the no-agent alert read the stored beacon value.

The v2 widget sends a beat every 30 s while the page is in view (chat open or
closed) with the seconds since the visitor last touched the page, `hidden`
when the page goes out of view, `leaving` on pagehide. "Left" is the silence
after a signal, never the signal alone (Zendesk, tawk.to, Comm100, Ably and
Pusher all work this way).
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.services.client_presence import (  # noqa: E402
    PRESENCE_CLOSE_AFTER_SECONDS,
    PRESENCE_V2_KEYS,
    client_left,
    derive_effective_presence,
    presence_changes_in,
    signal_patch,
)

T0 = datetime(2026, 11, 12, 14, 0, tzinfo=timezone.utc)
_CONV = "11111111-2222-3333-4444-555555555555"


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class Visit:
    """A conversation's metadata, written the way the database writes it:
    each signal's keys merged over the blob (metadata || patch)."""

    def __init__(self, mobile: bool = False):
        self.meta: dict = {}
        self.mobile = mobile
        self.typed = None

    def beat(self, t, chat="open", idle=0):
        self.meta.update(signal_patch("beat", at(t), chat, idle, int(self.mobile)))
        return self

    def hidden(self, t, idle=0):
        self.meta.update(signal_patch("hidden", at(t), None, idle, int(self.mobile)))
        return self

    def leaving(self, t, idle=0):
        self.meta.update(signal_patch("leaving", at(t), None, idle, int(self.mobile)))
        return self

    def message(self, t):
        self.typed = at(t).isoformat()
        return self

    def state(self, t):
        return derive_effective_presence(self.meta, at(t), self.typed)


# ════════════════════════════════════════════════════════════
# THE RULE
# ════════════════════════════════════════════════════════════

class TestOnTheSite:
    def test_chat_open_or_closed_is_said_as_it_is(self):
        assert Visit().beat(0, chat="open").state(5) == ("online", 5)
        assert Visit().beat(0, chat="closed").state(5) == ("minimized", 5)

    def test_a_reader_with_the_chat_closed_is_on_the_site(self):
        """The first rule had nothing to say about them: no ping while the
        chat was closed."""
        v = Visit()
        for t in range(0, 600, 30):
            v.beat(t, chat="closed", idle=t % 120)
        assert v.state(610)[0] == "minimized"

    @pytest.mark.parametrize("silent,expected", [
        (60, "online"), (89, "online"), (90, "stale"), (300, "stale"), (599, "stale"),
        (600, "left"), (3600, "left"),
    ])
    def test_silence_with_no_reason_given(self, silent, expected):
        """A beat every 30 s: two missed is "no signal", ten minutes of
        nothing at all (a killed tab, a phone that died) is gone."""
        state, age = Visit().beat(0).state(silent)
        assert state == expected
        assert age == silent

    @pytest.mark.parametrize("idle,expected", [
        (0, "online"), (599, "online"), (600, "idle"), (1199, "idle"), (1200, "left"),
    ])
    def test_a_page_nobody_touches(self, idle, expected):
        """The tab is in view and beating, but nobody moved, scrolled, typed
        or touched it (Zendesk: idle after 10 min; tawk.to and Zendesk end
        the session after 20)."""
        state, age = Visit().beat(0, idle=idle).state(0)
        assert state == expected
        if expected != "online":
            assert age == idle            # counted from the last activity
        # and the clock keeps running after the beat
        assert Visit().beat(0, idle=595).state(5)[0] == "idle"

    def test_a_message_is_a_beat_and_an_activity(self):
        """The client typing in the chat is there, whatever the beats said."""
        v = Visit().beat(0, idle=900).message(1100)
        assert v.state(1110) == ("online", 10)


class TestOutOfView:
    def test_another_tab_on_a_computer_is_away_until_twenty_minutes_without_activity(self):
        v = Visit().beat(0).hidden(10)
        assert v.state(70) == ("away", 60)
        assert v.state(10 + 1199)[0] == "away"
        state, age = v.state(10 + 1200)
        assert (state, age) == ("left", 1200)

    def test_idle_before_the_tab_switch_counts(self):
        v = Visit().beat(0, idle=900).hidden(20, idle=920)
        assert v.state(300)[0] == "left"           # 920 + 280 s without touching our page

    def test_a_phone_in_the_background_is_gone_after_two_minutes(self):
        """Zendesk: a phone's browser minimised or switched away ends the
        session in 20 s to 2 min."""
        v = Visit(mobile=True).beat(0).hidden(10)
        assert v.state(10 + 119) == ("away", 119)
        assert v.state(10 + 120) == ("left", 120)

    def test_back_in_view_cancels_it(self):
        v = Visit(mobile=True).beat(0).hidden(10).beat(70)
        assert v.state(75) == ("online", 5)
        assert v.state(500)[0] == "stale"          # and no longer "away"


class TestLeaving:
    def test_a_page_change_is_not_a_departure(self):
        """pagehide fires on every link the visitor follows; the next page
        beats within seconds."""
        v = Visit().beat(0).leaving(20).beat(23)
        for t in (25, 60, 200, 500):
            assert v.state(t)[0] != "left"
        assert v.state(30) == ("online", 7)

    def test_until_the_grace_runs_out_the_last_known_state_holds(self):
        v = Visit().beat(0, chat="closed").leaving(25)
        assert v.state(25 + 30) == ("minimized", 25)
        assert v.state(25 + 89)[0] == "minimized"

    def test_no_beat_after_leaving_is_a_departure_counted_from_the_leaving(self):
        v = Visit().beat(0).leaving(25)
        assert v.state(25 + 90) == ("left", 90)
        assert v.state(25 + 400) == ("left", 400)

    def test_a_beat_in_the_same_instant_cancels_the_leaving(self):
        """Seen in the browser run: the next page beat in the very millisecond
        of the pagehide's `leaving`."""
        v = Visit().beat(0).leaving(20).beat(20)
        assert v.state(20 + 120)[0] == "stale"     # silence, never "left" from that pagehide
        assert client_left(v.meta, now=at(20 + 300)) is None

    def test_signals_before_a_beat_are_history(self):
        v = Visit().beat(0).hidden(5).leaving(6).beat(40)
        assert v.state(45) == ("online", 5)


class TestShapes:
    def test_the_list_hands_booleans_and_numbers_as_text(self):
        meta = {k: str(val).lower() if isinstance(val, bool) else val
                for k, val in Visit(mobile=True).beat(0, chat="closed").hidden(5).meta.items()}
        meta["pres_v"] = "2"
        assert derive_effective_presence(meta, at(5 + 130))[0] == "left"
        meta["pres_hidden_at"] = None
        assert derive_effective_presence(meta, at(10))[0] == "minimized"

    def test_a_v2_conversation_with_no_signal_yet_says_nothing(self):
        assert derive_effective_presence({"pres_v": 2}, T0) == (None, None)

    def test_a_signal_from_the_future_is_now(self):
        assert Visit().beat(60).state(0) == ("online", 0)

    def test_older_widgets_keep_the_first_rule(self):
        """Visitors on a cached bundle send no pres_v: nothing about them
        changes, except that everything now reads the same derived state."""
        def v1(presence, age, pings=True):
            meta = {"widget_presence": presence, "widget_last_event_at": at(-age).isoformat()}
            if pings:
                meta["widget_pings"] = True
            return derive_effective_presence(meta, T0)
        assert v1("online", 60) == ("online", 60)
        assert v1("online", 95)[0] == "stale"
        assert v1("online", 1900)[0] == "left"
        assert v1("online", 7200, pings=False)[0] == "online"
        assert v1("minimized", 7200)[0] == "minimized"
        assert v1("left", 30) == ("left", 30)


def test_client_left_answers_since_when_or_none():
    assert client_left(Visit().beat(0).meta, now=at(30)) is None
    assert client_left(Visit().beat(0).leaving(10).meta, now=at(10 + 300)) == 300
    assert client_left({}, now=T0) is None
    assert client_left({"widget_presence": "left", "widget_last_event_at": at(-60).isoformat()}, now=T0) == 60


class TestWhenItChangesByItself:
    """Silence is never pushed: the panel reads the presence again when the
    server says its answer would change if nothing new came."""

    def changes(self, v, t):
        return presence_changes_in(v.meta, at(t), v.typed)

    def test_after_a_beat_the_next_change_is_no_signal(self):
        v = Visit().beat(0)
        assert self.changes(v, 0) == 91
        assert v.state(91)[0] == "stale" and v.state(90 - 1)[0] == "online"

    def test_then_left_at_ten_minutes(self):
        v = Visit().beat(0)
        assert self.changes(v, 100) == 600 - 100 + 1
        assert v.state(100 + 501)[0] == "left"

    def test_out_of_view_on_a_phone_is_two_minutes(self):
        v = Visit(mobile=True).beat(0).hidden(10)
        assert self.changes(v, 10) == 121

    def test_a_page_change_grace(self):
        v = Visit().beat(0).leaving(20)
        assert self.changes(v, 20) == 91

    def test_inactive_then_gone(self):
        v = Visit().beat(0, idle=500)
        assert self.changes(v, 0) == 91          # no signal comes first
        v.beat(60, idle=560)
        assert self.changes(v, 60) == 41         # inactive at 600 s
        v.beat(90, idle=650)                      # now inactive …
        assert v.state(90)[0] == "idle"
        assert self.changes(v, 90) == 91          # … then no signal if beats stop

    def test_left_stays_left_and_unknown_stays_unknown(self):
        assert self.changes(Visit().beat(0).leaving(10), 200) is None
        assert presence_changes_in({}, T0) is None
        assert presence_changes_in({"widget_presence": "minimized",
                                    "widget_last_event_at": T0.isoformat()}, T0) is None

    def test_the_first_rule_too(self):
        meta = {"widget_presence": "online", "widget_pings": True, "widget_last_event_at": T0.isoformat()}
        assert presence_changes_in(meta, T0) == 91
        assert presence_changes_in(meta, at(100)) == 1800 - 100 + 1


# ════════════════════════════════════════════════════════════
# THE SIGNALS
# ════════════════════════════════════════════════════════════

class TestSignalPatch:
    def test_a_beat_says_everything_the_rule_needs(self):
        p = signal_patch("beat", T0, "closed", 45, 1)
        assert p["pres_v"] == 2
        assert p["pres_seen_at"] == T0.isoformat()
        assert p["pres_active_at"] == at(-45).isoformat()
        assert p["pres_chat_open"] is False and p["pres_mobile"] is True
        # The first rule's keys say the same thing.
        assert (p["widget_open"], p["widget_presence"], p["widget_pings"]) == (False, "minimized", True)
        assert set(p) - {"widget_open", "widget_presence", "widget_last_event",
                         "widget_last_event_at", "widget_pings"} <= set(PRESENCE_V2_KEYS)

    def test_hidden_touches_nothing_but_its_own_keys(self):
        p = signal_patch("hidden", T0, None, 3, 0)
        assert set(p) == {"pres_v", "pres_hidden_at", "pres_active_at", "pres_mobile"}

    def test_leaving_also_writes_the_first_rules_left(self):
        p = signal_patch("leaving", T0, None, 0, 0)
        assert p["pres_leaving_at"] == T0.isoformat()
        assert (p["widget_presence"], p["widget_open"], p["widget_last_close_reason"]) == ("left", False, "left")
        assert "pres_seen_at" not in p

    def test_idle_is_clamped_to_a_day_and_never_negative(self):
        assert signal_patch("beat", T0, "open", -50, 0)["pres_active_at"] == T0.isoformat()
        assert signal_patch("beat", T0, "open", 10**9, 0)["pres_active_at"] == at(-86_400).isoformat()

    def test_a_signal_without_idle_or_device_leaves_them_alone(self):
        assert "pres_active_at" not in signal_patch("hidden", T0, None, None, None)
        assert "pres_mobile" not in signal_patch("beat", T0, "open", None, None)
        assert signal_patch("beat", T0, "open", None, None)["pres_active_at"] == T0.isoformat()


class TestPingEndpoint:
    @pytest.mark.asyncio
    async def test_an_older_widget_ping_writes_what_it_always_wrote(self):
        """The branch NOT taken: no v → none of the new keys, the old meaning."""
        from app.api.chat import ping_chat_session

        write = AsyncMock(return_value=True)
        with patch("app.db.supabase.patch_conversation_presence", new=write), \
                patch("app.realtime.manager.manager.push", new=AsyncMock()) as push:
            out = await ping_chat_session(_CONV, _owner=None)
        assert out == {"success": True}
        written = write.await_args.args[1]
        assert set(written) == {"widget_open", "widget_presence", "widget_last_event",
                                "widget_last_event_at", "widget_pings"}
        assert written["widget_presence"] == "online"
        event = push.await_args.args[1]
        assert (event["widget_presence_effective"], event["widget_presence_age_seconds"]) == ("online", 0)

    @pytest.mark.asyncio
    async def test_a_beat_with_the_chat_closed_is_pushed_as_on_the_site_chat_closed(self):
        from app.api.chat import ping_chat_session

        with patch("app.db.supabase.patch_conversation_presence", new=AsyncMock(return_value=True)) as write, \
                patch("app.realtime.manager.manager.push", new=AsyncMock()) as push:
            out = await ping_chat_session(_CONV, v=2, kind="beat", chat="closed", idle=12, m=0, _owner=None)
        assert out == {"success": True}
        assert write.await_args.args[1]["pres_chat_open"] is False
        event = push.await_args.args[1]
        assert event["event"] == "presence"
        assert event["widget_presence_effective"] == "minimized"
        assert event["widget_open"] is False
        assert event["widget_presence_changes_in"] == 91

    @pytest.mark.asyncio
    async def test_hidden_is_pushed_as_away(self):
        from app.api.chat import ping_chat_session

        with patch("app.db.supabase.patch_conversation_presence", new=AsyncMock(return_value=True)), \
                patch("app.realtime.manager.manager.push", new=AsyncMock()) as push:
            await ping_chat_session(_CONV, v=2, kind="hidden", idle=3, m=1, _owner=None)
        event = push.await_args.args[1]
        assert event["widget_presence_effective"] == "away"
        # It says nothing about the chat window: those keys keep their value.
        assert "widget_open" not in event and "widget_presence" not in event

    @pytest.mark.asyncio
    async def test_leaving_clears_typing_and_does_not_announce_a_departure(self):
        """Most pagehides are page changes: the presence line holds until the
        grace runs out (the panel re-reads it)."""
        from app.api.chat import ping_chat_session

        with patch("app.db.supabase.patch_conversation_presence", new=AsyncMock(return_value=True)), \
                patch("app.realtime.typing_indicator.typing_manager.clear_typing", new=AsyncMock()) as clear, \
                patch("app.realtime.manager.manager.push", new=AsyncMock()) as push:
            out = await ping_chat_session(_CONV, v=2, kind="leaving", idle=0, m=0, _owner=None)
        assert out == {"success": True}
        clear.assert_awaited_once_with(_CONV)
        assert [c.args[1]["event"] for c in push.await_args_list] == ["typing"]

    @pytest.mark.asyncio
    async def test_a_failed_write_pushes_nothing(self):
        from app.api.chat import ping_chat_session

        with patch("app.db.supabase.patch_conversation_presence", new=AsyncMock(return_value=False)), \
                patch("app.realtime.manager.manager.push", new=AsyncMock()) as push:
            out = await ping_chat_session(_CONV, v=2, kind="hidden", _owner=None)
        assert out == {"success": False}
        push.assert_not_awaited()


# ════════════════════════════════════════════════════════════
# EVERYTHING READS THE SAME RULE
# ════════════════════════════════════════════════════════════

class _Rows:
    """conversations.select(...).in_(...).execute() → these rows."""

    def __init__(self, rows):
        self.rows = rows
        self.selected = None

    def table(self, _name):
        return self

    def select(self, columns):
        self.selected = columns
        return self

    def in_(self, *_a):
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()


def _ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


@pytest.mark.asyncio
async def test_the_sweep_closes_by_the_rule_five_minutes_after_the_client_was_last_there():
    from app.db import supabase as sb

    def v2(**signals):
        meta = {"pres_v": 2}
        meta.update({f"pres_{k}": _ago(s) for k, s in signals.items() if k != "mobile"})
        meta["pres_mobile"] = signals.get("mobile", False)
        return meta

    rows = [
        {"id": "closed-tab-6min", "metadata": v2(seen_at=400, active_at=400, leaving_at=360)},
        {"id": "closed-tab-3min", "metadata": v2(seen_at=200, active_at=200, leaving_at=180)},
        {"id": "page-change", "metadata": v2(seen_at=10, active_at=10, leaving_at=13)},
        {"id": "killed-11min", "metadata": v2(seen_at=660, active_at=660)},
        {"id": "phone-away-4min", "metadata": v2(seen_at=250, active_at=250, hidden_at=240, mobile=True)},
        {"id": "phone-away-6min", "metadata": v2(seen_at=370, active_at=370, hidden_at=360, mobile=True)},
        {"id": "reading-chat-closed", "metadata": {**v2(seen_at=20, active_at=60), "pres_chat_open": False}},
        # The first rule, for older widgets:
        {"id": "v1-left-6min", "metadata": {"widget_presence": "left", "widget_last_event_at": _ago(360)}},
        {"id": "v1-lost-beacon-40min", "metadata": {"widget_presence": "online", "widget_pings": True,
                                                    "widget_last_event_at": _ago(2400)}},
        {"id": "v1-never-pinged", "metadata": {"widget_presence": "online", "widget_last_event_at": _ago(7200)}},
        {"id": "v1-minimized", "metadata": {"widget_presence": "minimized", "widget_last_event_at": _ago(7200)}},
        {"id": "nothing-known", "metadata": {}},
    ]
    db = _Rows(rows)
    with patch.object(sb, "get_client", return_value=db), \
            patch.object(sb, "_supervisor_columns_available", return_value=True):
        out = await sb.get_stale_presence_left_conversations(PRESENCE_CLOSE_AFTER_SECONDS // 60)
    assert sorted(r["id"] for r in out) == [
        "closed-tab-6min", "killed-11min", "phone-away-6min",
        "v1-left-6min", "v1-lost-beacon-40min",
    ]
    # A message is presence too: the sweep reads the client's last one.
    assert "last_user_message_at" in db.selected


@pytest.mark.asyncio
async def test_a_client_who_just_wrote_is_never_swept():
    from app.db import supabase as sb

    rows = [{"id": "typing", "last_user_message_at": _ago(20),
             "metadata": {"pres_v": 2, "pres_seen_at": _ago(1200), "pres_leaving_at": _ago(1100)}}]
    with patch.object(sb, "get_client", return_value=_Rows(rows)), \
            patch.object(sb, "_supervisor_columns_available", return_value=True):
        assert await sb.get_stale_presence_left_conversations(5) == []


def test_no_one_reads_the_stored_left_any_more():
    """The queue (agent.py), the next chat after a close (conversations.py),
    the no-agent alert (orchestrator.py) and "Your day" (agent_day.py) read
    the rule the panel shows; the stored value is only the first rule's input."""
    root = os.path.join(os.path.dirname(__file__), "..", "app")
    stale_reads, rule_reads = [], 0
    for folder, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".py"):
                continue
            src = open(os.path.join(folder, name), encoding="utf-8").read()
            if 'get("widget_presence") == "left"' in src or 'get("widget_presence") != "left"' in src:
                stale_reads.append(name)
            rule_reads += src.count("client_left(")
    assert stale_reads == []
    # four consumers + the sweep + the definition
    assert rule_reads >= 6


def test_your_day_says_how_long_ago_the_client_left_by_the_rule():
    from app.services.agent_day import build_agent_day

    now = datetime.now(timezone.utc)
    convs = [{
        "id": "c", "visitor_name": "Test Person", "last_user_message_at": (now - timedelta(minutes=30)).isoformat(),
        "last_agent_message_at": (now - timedelta(minutes=40)).isoformat(),
        # A phone that went to the background 7 minutes ago and never came back.
        "metadata": {"pres_v": 2, "pres_mobile": True,
                     "pres_seen_at": (now - timedelta(minutes=8)).isoformat(),
                     "pres_hidden_at": (now - timedelta(minutes=7)).isoformat()},
    }]
    d = build_agent_day(now=now, offset_minutes=0, days=1, activity=[], agent_messages=[],
                        conversations={}, leads={}, open_conversations=convs)
    assert d["waiting_on_you"][0]["left_minutes_ago"] == 7


# ════════════════════════════════════════════════════════════
# WHOLE VISITS, SIGNAL BY SIGNAL
# ════════════════════════════════════════════════════════════

def test_a_visit_on_a_computer():
    v = Visit()
    v.beat(0, chat="closed")                       # reads the offer, chat closed
    assert v.state(10)[0] == "minimized"
    v.beat(30, chat="open").message(40)             # opens the chat and writes
    assert v.state(45)[0] == "online"
    v.leaving(60).beat(62, chat="open")             # follows a link: next page
    assert v.state(70)[0] == "online"
    v.hidden(90)                                    # another tab, for 8 minutes
    assert v.state(90 + 480) == ("away", 480)
    v.beat(90 + 480)                                # back
    assert v.state(90 + 485)[0] == "online"
    v.leaving(600)                                  # closes the tab
    assert v.state(600 + 60)[0] == "online"         # the grace: maybe a page change
    assert v.state(600 + 90) == ("left", 90)
    assert client_left(v.meta, v.typed, at(600 + 299)) == 299        # the sweep waits…
    assert client_left(v.meta, v.typed, at(600 + 300)) == 300        # …and closes


def test_a_tab_left_open_on_a_desk_overnight():
    """The widget stops beating after 20 idle minutes; silence after that
    must not turn a departure back into "no signal"."""
    v = Visit()
    for t in range(0, 1200, 30):
        v.beat(t, idle=t)
    assert v.state(700)[0] == "idle"
    assert v.state(1230)[0] == "left"
    assert v.state(8 * 3600)[0] == "left"


def test_a_phone_that_dies_without_a_word():
    v = Visit(mobile=True).beat(0)
    assert v.state(100)[0] == "stale"
    assert v.state(600)[0] == "left"


@pytest.mark.asyncio
async def test_the_live_presence_line_reads_the_rule_and_says_when_it_changes():
    from app.api import conversations as api

    now = datetime.now(timezone.utc)
    conv = {"id": _CONV, "tunnel": "sales", "updated_at": now.isoformat(), "last_user_message_at": None,
            "metadata": {"pres_v": 2, "pres_mobile": True, "pres_chat_open": True,
                         "pres_seen_at": (now - timedelta(seconds=40)).isoformat(),
                         "pres_active_at": (now - timedelta(seconds=40)).isoformat(),
                         "pres_hidden_at": (now - timedelta(seconds=30)).isoformat()}}
    with patch.object(api.db, "get_conversation_simple", new=AsyncMock(return_value=conv)):
        out = await api.get_conversation_presence(_CONV, user={"role": "admin", "tunnel_scope": "all"})
    data = out["data"]
    assert data["widget_presence_effective"] == "away"
    assert 29 <= data["widget_presence_age_seconds"] <= 32
    # A phone out of view: gone at two minutes, so read again in about 90 s.
    assert 88 <= data["widget_presence_changes_in"] <= 92
