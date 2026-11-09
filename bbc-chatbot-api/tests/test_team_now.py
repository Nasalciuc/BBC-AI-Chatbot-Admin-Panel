"""Team · Now — the floor right now, and one person's day.

What is pinned here:
  * Ready, "sends no signal", online and offline follow the server's own
    windows (90 s out of the queue, Ready off at 10 min);
  * the queue is the shared line's rule; a client waiting in a person's chat
    is their last message newer than the person's;
  * Needs attention only past a line: a client waiting 5 min, a Ready panel
    with no signal, a queue over 3 min, one person Ready in a tunnel, a chat
    back to the assistant today — and a chat answered in time is not one;
  * a supervisor sees their team's people and chats, their tunnel's queue
    masked, the AI's chats only as a number, no previews; no team means the
    "no team yet" state; another team's person is 403;
  * Ready today is cut at the viewer's midnight, from the state before it;
  * a failed read is a 502.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.security.auth import get_current_user  # noqa: E402
from app.services.team_now import (  # noqa: E402
    build_person_day,
    build_team_now,
    presence_of,
    ready_spans,
)

# Wednesday 4 Nov 2026, 14:12 in Chisinau (+120).
NOW = datetime(2026, 11, 4, 12, 12, tzinfo=timezone.utc)
OFFSET = 120


def ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


def user(uid, name, role="sales", **over):
    row = {"id": uid, "name": name, "email": f"{uid}@buybusinessclass.com", "role": role, "tunnel_scope": role,
           "team_id": "team-day", "is_ready": True, "last_seen_at": ago(seconds=5), "is_active": True}
    row.update(over)
    return row


USERS = [
    user("rohan", "Rohan Mehta"),
    user("victor", "Victor Rusu"),
    user("ana", "Ana Ceban", last_seen_at=ago(minutes=4)),                     # Ready, no signal
    user("ion", "Ion Ciobanu", role="support", team_id="team-support", is_ready=False, last_seen_at=ago(minutes=52)),
    user("priya", "Priya Nair", role="support", team_id="team-support"),
    user("tudor", "Tudor Lupu", role="support", team_id="team-support", is_ready=False, last_seen_at=ago(minutes=2)),
    user("irina", "Irina Moraru", role="supervisor", tunnel_scope="sales"),
]
TEAMS = [
    {"id": "team-day", "name": "Sales Day", "shift_start": "12:00:00", "shift_end": "20:00:00", "supervisor_id": "irina"},
    {"id": "team-support", "name": "Support", "shift_start": "09:00:00", "shift_end": "17:00:00"},
]


def conv(cid, **over):
    row = {"id": cid, "chat_number": 4800, "tunnel": "sales", "status": "active", "mode": "ai",
           "created_at": ago(minutes=20), "queued_at": None, "assigned_agent_id": None, "team_id": None,
           "visitor_name": f"Client {cid.title()}", "last_user_message_at": None, "last_agent_message_at": None,
           "message_count": 6}
    row.update(over)
    return row


def floor_inputs():
    convs = [
        conv("queued", queued_at=ago(minutes=9), status="needs_agent", visitor_name="Grace Miller"),
        conv("queued-support", tunnel="support", queued_at=ago(seconds=50)),
        conv("waiting-long", mode="human", assigned_agent_id="rohan", team_id="team-day",
             visitor_name="Sophia Reed", last_user_message_at=ago(minutes=34), last_agent_message_at=ago(minutes=40)),
        conv("answered", mode="human", assigned_agent_id="victor", team_id="team-day",
             last_user_message_at=ago(minutes=8), last_agent_message_at=ago(minutes=7)),
        conv("ai-1"), conv("ai-2", tunnel="support"),
        conv("closed-one", status="closed"),
    ]
    activity = [
        {"user_id": "victor", "conversation_id": "answered", "action": "claim_won", "happened_at": ago(minutes=30)},
        {"user_id": "ion", "conversation_id": "back", "action": "claim_won", "happened_at": ago(minutes=84)},
        {"user_id": "ion", "conversation_id": "back", "action": "deadline_fired", "happened_at": ago(minutes=82)},
        # Yesterday: not today's.
        {"user_id": "rohan", "conversation_id": "old", "action": "deadline_fired", "happened_at": ago(hours=20)},
    ]
    agent_msgs = [{"conversation_id": "answered", "created_at": ago(minutes=29, seconds=10)}]
    leads = [{"conversation_id": "queued", "origin_code": "BOS", "destination_code": "DXB", "route_display": None}]
    ready_log = [{"user_id": "rohan", "is_ready": True, "changed_at": ago(minutes=64)}]
    return dict(users=USERS, teams=TEAMS, conversations=convs, leads=leads, activity=activity,
                agent_msgs=agent_msgs, ready_log=ready_log)


def build(**over):
    kw = dict(now=NOW, offset_minutes=OFFSET, **floor_inputs())
    kw.update(over)
    return build_team_now(**kw)


def test_a_team_card_says_who_and_how_many_were_ready_each_hour():
    d = build()
    day = next(t for t in d["teams"] if t["id"] == "team-day")
    assert day["supervisor_name"] == "Irina Moraru" and {m["name"] for m in day["members"]} == {"Rohan Mehta", "Victor Rusu", "Ana Ceban"}
    assert (day["ready_now"], day["won_today"], day["back_today"]) == (2, 1, 0)
    support = next(t for t in d["teams"] if t["id"] == "team-support")
    assert support["back_today"] == 1
    # 14:12 in Chisinau: hours 0–14 are known, the rest is still to come.
    assert len(day["coverage"]) == 24 and day["coverage"][15] is None and day["coverage"][14] is not None
    # Rohan Ready since 13:08: counted at 13 and 14; with no change logged,
    # Victor and Ana are Ready since midnight.
    assert day["coverage"][13] == 3 and day["coverage"][0] == 2


def test_presence_follows_the_servers_windows():
    assert presence_of(user("a", "A"), NOW, queue_window_s=90, online_s=600) == "ready"
    assert presence_of(user("a", "A", last_seen_at=ago(seconds=120)), NOW, queue_window_s=90, online_s=600) == "no_signal"
    assert presence_of(user("a", "A", is_ready=False, last_seen_at=ago(minutes=3)), NOW, queue_window_s=90, online_s=600) == "online"
    assert presence_of(user("a", "A", is_ready=False, last_seen_at=ago(minutes=30)), NOW, queue_window_s=90, online_s=600) == "offline"


def test_the_floor_by_chat():
    d = build()
    assert [q["id"] for q in d["queue"]] == ["queued", "queued-support"]
    assert d["queue"][0]["asked_for_person"] is True and d["queue"][0]["route"] == "BOS → DXB"
    assert [w["id"] for w in d["with_person"]] == ["waiting-long", "answered"]
    assert d["with_person"][0]["client_waiting"] is True and d["with_person"][1]["client_waiting"] is False
    assert d["ai_only"]["count"] == 2 and "closed-one" not in {c["id"] for c in d["ai_only"]["chats"]}
    s = d["stats"]
    assert (s["ready"], s["no_signal"], s["waiting"], s["back_today"]) == (3, 1, 2, 1)
    assert s["first_reply_today_s"] == 50 and s["oldest_waiting"]["client"] == "Grace M."


def test_people_and_the_chats_they_have_carry_their_photo():
    photo = "https://example.com/avatars/rohan.jpg"
    users = [user("rohan", "Rohan Mehta", avatar_url=f"  {photo} "), *(u for u in USERS if u["id"] != "rohan")]
    d = build(users=users)
    people = {p["id"]: p["avatar_url"] for p in d["people"]}
    assert people["rohan"] == photo and people["victor"] is None
    chats = {w["id"]: w["agent_avatar_url"] for w in d["with_person"]}
    assert chats == {"waiting-long": photo, "answered": None}
    day = build_person_day(
        now=NOW, offset_minutes=OFFSET, user=users[0], team=TEAMS[0], masked=False, activity=[], agent_msgs=[],
        conversations={}, ready_log=[], presence_days=[], open_chats=[],
    )
    assert day["user"]["avatar_url"] == photo
    blank = build_person_day(
        now=NOW, offset_minutes=OFFSET, user=user("victor", "Victor Rusu", avatar_url="  "), team=None, masked=False,
        activity=[], agent_msgs=[], conversations={}, ready_log=[], presence_days=[], open_chats=[],
    )
    assert blank["user"]["avatar_url"] is None


def test_needs_attention_only_past_a_line():
    kinds = [(a["kind"], a.get("name") or a.get("tunnel")) for a in build()["attention"]]
    assert kinds == [
        ("client_waiting", "Rohan Mehta"),   # 34 min without a reply
        ("no_signal", "Ana Ceban"),
        ("queue_wait", "sales"),             # Grace has waited 9 min
        ("coverage", "Priya Nair"),          # the only one Ready in support
        ("went_back", "Ion Ciobanu"),        # today, not Rohan's from yesterday
    ]
    # Victor answered a minute after the client: not waiting. The support
    # queue is 50 s old: under the line.
    attention = build()["attention"]
    assert all(a.get("conversation_id") != "answered" for a in attention)
    assert all(not (a["kind"] == "queue_wait" and a["tunnel"] == "support") for a in attention)


def test_a_supervisor_sees_their_team_and_their_tunnels_queue_masked():
    d = build(team_ids={"team-day"}, tunnel="sales", masked=True, previews={"queued": {"content": "+1 555 0110"}})
    assert {p["id"] for p in d["people"]} == {"rohan", "victor", "ana"}
    assert [q["id"] for q in d["queue"]] == ["queued"]
    assert d["queue"][0]["client"].startswith("•••") and d["queue"][0]["preview"] is None
    assert d["ai_only"] == {"count": 1, "chats": []}
    assert {t["name"] for t in d["teams"]} == {"Sales Day"}
    assert all(a["kind"] != "coverage" for a in d["attention"])


def test_ready_today_starts_from_the_state_before_midnight():
    midnight = datetime(2026, 11, 3, 22, 0, tzinfo=timezone.utc)   # 00:00 in Chisinau
    log = [
        {"user_id": "u", "is_ready": True, "changed_at": "2026-11-03T20:00:00+00:00"},
        {"user_id": "u", "is_ready": False, "changed_at": "2026-11-04T08:00:00+00:00"},
        {"user_id": "u", "is_ready": True, "changed_at": "2026-11-04T11:08:00+00:00"},
    ]
    assert ready_spans("u", log, midnight, fallback=False) == [
        ["2026-11-03T22:00:00+00:00", "2026-11-04T08:00:00+00:00"],
        ["2026-11-04T11:08:00+00:00", None],
    ]
    # No change before midnight in the read: a first "not ready" means Ready since midnight.
    assert ready_spans("u", log[1:2], midnight, fallback=False)[0][0] == "2026-11-03T22:00:00+00:00"
    assert ready_spans("u", [], midnight, fallback=True) == [["2026-11-03T22:00:00+00:00", None]]
    assert ready_spans("u", [], midnight, fallback=False) == []


def test_one_persons_day():
    d = build_person_day(
        now=NOW, offset_minutes=OFFSET, user=USERS[0], team=TEAMS[0], masked=False,
        activity=[
            {"user_id": "rohan", "conversation_id": "c1", "action": "claim_won", "happened_at": ago(minutes=60)},
            {"user_id": "rohan", "conversation_id": "c1", "action": "deadline_fired", "happened_at": ago(minutes=58)},
            {"user_id": "rohan", "conversation_id": "c2", "action": "claim_won", "happened_at": ago(minutes=52)},
            {"user_id": "rohan", "conversation_id": "c3", "action": "claim_lost", "happened_at": ago(minutes=50)},
        ],
        agent_msgs=[{"conversation_id": "c2", "created_at": ago(minutes=50)}],
        conversations={"c1": {"id": "c1", "visitor_name": "Lucas Brown"}, "c2": {"id": "c2", "visitor_name": "Sophia Reed"}},
        ready_log=[{"user_id": "rohan", "is_ready": True, "changed_at": ago(minutes=64)}],
        presence_days=[{"day": "2026-11-04", "minutes_online": 122, "first_seen_at": ago(minutes=127)},
                       {"day": "2026-11-02", "minutes_online": 456}],
        open_chats=[conv("c2", assigned_agent_id="rohan", visitor_name="Sophia Reed",
                         last_user_message_at=ago(minutes=34), last_agent_message_at=ago(minutes=50))],
    )
    t = d["today"]
    assert (t["chats_now"], t["took"], t["replied_to"], t["went_back"], t["takes_lost"]) == (1, 2, 1, 1, 1)
    assert t["first_reply_median_s"] == 120 and t["minutes_online"] == 122
    assert d["user"]["team"]["name"] == "Sales Day" and d["user"]["presence"] == "ready"
    assert [w["minutes_online"] for w in d["week_online"]] == [0, 0, 0, 0, 456, 0, 122]
    assert d["week_online"][-1]["date"] == "2026-11-04"
    kinds = [e["kind"] for e in d["activity"]]
    assert kinds[0] == "client_waiting" and {"took", "replied", "went_back", "take_lost", "ready"} <= set(kinds)
    assert next(e for e in d["activity"] if e["kind"] == "went_back")["client"] == "Lucas B."


# ── The endpoints ────────────────────────────────────────────────────


def _client(role: str, uid: str = "irina", tunnel_scope: str = "sales") -> TestClient:
    from app.api.teams import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": uid, "role": role, "tunnel_scope": tunnel_scope}
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_cache():
    from app.api import teams

    teams._TEAM_NOW_CACHE.clear()
    yield
    teams._TEAM_NOW_CACHE.clear()


@pytest.mark.parametrize("role", ["sales", "support", "qa"])
def test_team_is_not_for_agents_or_qa(role):
    assert _client(role).get("/api/admin/team/now").status_code == 403
    assert _client(role).get("/api/admin/team/people/rohan/today").status_code == 403


def test_a_supervisor_without_a_team_gets_the_no_team_state():
    read = AsyncMock(return_value=floor_inputs())
    with patch("app.db.supabase.get_team_now_inputs", new=read), \
         patch("app.db.supabase.get_team_ids_for_supervisor", new=AsyncMock(return_value=[])):
        body = _client("supervisor").get("/api/admin/team/now").json()
    assert body["data"]["no_team"] is True and read.await_count == 0


def test_the_owner_sees_everyone_with_previews_and_the_read_is_shared():
    read = AsyncMock(return_value=floor_inputs())
    previews = AsyncMock(return_value={"queued": {"content": "Can a person help me?"}})
    with patch("app.db.supabase.get_team_now_inputs", new=read), \
         patch("app.db.supabase.get_last_message_previews", new=previews):
        a = _client("owner", "own").get("/api/admin/team/now?tz_offset_minutes=120").json()["data"]
        _client("admin", "adm").get("/api/admin/team/now?tz_offset_minutes=120")
    assert read.await_count == 1
    assert len(a["people"]) == 6 and a["queue"][0]["preview"] == "Can a person help me?"
    assert a["can_give"] is True


def test_a_project_manager_gets_no_previews_and_cant_give():
    previews = AsyncMock(return_value={"queued": {"content": "x"}})
    with patch("app.db.supabase.get_team_now_inputs", new=AsyncMock(return_value=floor_inputs())), \
         patch("app.db.supabase.get_team_ids_for_pm", new=AsyncMock(return_value=["team-day", "team-support"])), \
         patch("app.db.supabase.get_last_message_previews", new=previews):
        d = _client("project_manager", "pm", tunnel_scope="all").get("/api/admin/team/now").json()["data"]
    assert previews.await_count == 0 and d["can_give"] is False
    assert d["queue"][0]["client"] == "Grace M."


def test_a_failed_read_is_a_502():
    with patch("app.db.supabase.get_team_now_inputs", new=AsyncMock(side_effect=RuntimeError("boom"))):
        assert _client("owner", "own").get("/api/admin/team/now").status_code == 502


def test_another_teams_person_is_403():
    inputs = {"user": USERS[3], "team": TEAMS[1], "activity": [], "agent_msgs": [], "conversations": {},
              "ready_log": [], "presence_days": [], "open_chats": []}
    with patch("app.db.supabase.get_person_day_inputs", new=AsyncMock(return_value=inputs)), \
         patch("app.db.supabase.get_team_ids_for_supervisor", new=AsyncMock(return_value=["team-day"])):
        assert _client("supervisor").get("/api/admin/team/people/ion/today").status_code == 403
    with patch("app.db.supabase.get_person_day_inputs", new=AsyncMock(return_value=None)):
        assert _client("owner", "own").get("/api/admin/team/people/nope/today").status_code == 404
