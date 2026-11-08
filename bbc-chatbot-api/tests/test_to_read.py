"""To read — the chats a supervisor or the owner should read this week.

What is pinned here:
  * "Anyone there?" is read with fixed rules on what clients actually write,
    and an ordinary question is not one;
  * a chat is lost by fixed rules on its takes: went back to the assistant,
    the client asked if anyone was there, the person never wrote, the person
    came in after the client's YES — and a chat answered in time is not lost;
  * the week starts Monday 00:00 in the reader's time zone;
  * last week's sample is two chats per person, stable all week (a hash, no
    table), lost chats left out, one more past the Dashboard's help line;
  * a supervisor sees only their team's chats, names and contacts masked —
    in the messages too; no team means nothing; other teams' chats are 403;
  * a failed read is a 502, never an empty list.
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
from app.services.to_read import (  # noqa: E402
    build_reading,
    build_to_read,
    client_label,
    is_anyone_there,
    mask_contacts,
    week_start,
)

# Friday 6 Nov 2026, 15:30 in New York (-300).
NOW = datetime(2026, 11, 6, 20, 30, tzinfo=timezone.utc)
OFFSET = -300
ROHAN, VICTOR, ANA = "rohan", "victor", "ana"
USERS = {
    ROHAN: {"id": ROHAN, "name": "Rohan Mehta", "role": "sales"},
    VICTOR: {"id": VICTOR, "name": "Victor Rusu", "role": "sales"},
    ANA: {"id": ANA, "name": "Ana Ceban", "role": "sales"},
}


def at(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


@pytest.mark.parametrize("text", [
    "?", "??", "???", "Are you there", "Are you there?", "are you still there?", "Still there?",
    "Are you still there im going to fall asleep", "I'm  waiting", "Hello?", "anyone there?",
    "Is anybody available", "im still waiting for you",
])
def test_anyone_there_on_what_clients_write(text):
    assert is_anyone_there(text)


@pytest.mark.parametrize("text", [
    "How long is the flight?", "Is the hotel included?", "I'm waiting for my visa to be approved",
    "Hello, I need two seats", "Where are you based?", "", None,
])
def test_an_ordinary_question_is_not_anyone_there(text):
    assert not is_anyone_there(text)


def test_the_week_starts_monday_in_the_readers_time_zone():
    assert week_start(NOW, OFFSET) == datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)
    # Monday 01:00 in Chisinau (+120) is still Sunday in UTC.
    monday_night = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)
    assert week_start(monday_night, 120) == datetime(2026, 11, 1, 22, 0, tzinfo=timezone.utc)


def test_client_label():
    assert client_label("Olga Vasilescu", masked=False) == "Olga V."
    assert client_label("Olga", masked=False) == "Olga"
    assert client_label("Olga Vasilescu", masked=True) == "•••cu"
    assert client_label(None, masked=False) == ""


def test_contacts_in_a_message_are_masked_but_dates_and_prices_stay():
    text = "Send it to olga.v@example.com or call +1 (555) 012-3377. Fly 2026-11-20, about $8,400."
    out = mask_contacts(text)
    assert "olga.v@example.com" not in out and "ol•••@example.com" in out
    assert "012-3377" not in out and "•••3377" in out
    assert "2026-11-20" in out and "$8,400" in out


# ── The list ─────────────────────────────────────────────────────────


def conv(cid, **over):
    row = {"id": cid, "chat_number": 4800, "visitor_name": f"Client {cid.title()}", "tunnel": "sales",
           "status": "closed", "created_at": at(days=1), "closed_at": None, "team_id": "team-1",
           "confirmed_at": None}
    row.update(over)
    return row


def scenario():
    """This week (since Mon 2 Nov): four lost chats and one answered in time.
    Last week: Victor's and Rohan's ordinary chats, one of Rohan's lost."""
    convs, activity, messages, leads = [], [], [], []

    def take(cid, who, when, end=None, end_after_s=None, reply_after_s=None, team="team-1", **c):
        convs.append(conv(cid, team_id=team, **c))
        t0 = NOW - when
        activity.append({"user_id": who, "conversation_id": cid, "action": "claim_won", "happened_at": t0.isoformat()})
        if end:
            activity.append({"user_id": who, "conversation_id": cid, "action": end,
                             "happened_at": (t0 + timedelta(seconds=end_after_s)).isoformat()})
        if reply_after_s is not None:
            messages.append({"conversation_id": cid, "role": "agent", "content": "Hi",
                             "created_at": (t0 + timedelta(seconds=reply_after_s)).isoformat()})
        leads.append({"conversation_id": cid, "origin_code": "JFK", "destination_code": "DXB", "route_display": "New York → Dubai"})
        return t0

    take("back", ROHAN, timedelta(hours=1), end="deadline_fired", end_after_s=95)
    t0 = take("anyone", ROHAN, timedelta(hours=26), end="closed", end_after_s=900, reply_after_s=40)
    messages.append({"conversation_id": "anyone", "role": "user", "content": "Are you still there?",
                     "created_at": (t0 + timedelta(seconds=400)).isoformat()})
    take("never", ANA, timedelta(hours=50), end="closed", end_after_s=300)
    take("after-yes", VICTOR, timedelta(hours=3), end="closed", end_after_s=600, reply_after_s=20,
         confirmed_at=at(hours=3, minutes=5))
    take("fine", VICTOR, timedelta(hours=2), end="closed", end_after_s=600, reply_after_s=15)
    # A client's ordinary question while a person had the chat is not "anyone there".
    messages.append({"conversation_id": "fine", "role": "user", "content": "Is the hotel included?",
                     "created_at": at(hours=1, minutes=58)})
    # Another team's chat.
    take("other-team", ROHAN, timedelta(hours=4), end="deadline_fired", end_after_s=95, team="team-2")
    # Last week: ordinary chats, and one of Rohan's that went back.
    for i in range(5):
        take(f"v-{i}", VICTOR, timedelta(days=7, hours=i), end="closed", end_after_s=600, reply_after_s=20)
    for i in range(4):
        take(f"r-{i}", ROHAN, timedelta(days=8, hours=i), end="closed", end_after_s=600, reply_after_s=80)
    take("r-lost", ROHAN, timedelta(days=9), end="deadline_fired", end_after_s=95)
    take("r-lost-2", ROHAN, timedelta(days=9, hours=2), end="deadline_fired", end_after_s=95)
    return dict(activity=activity, conversations=convs, leads=leads, messages=messages, users=USERS)


def build(**over):
    kw = dict(now=NOW, offset_minutes=OFFSET, **scenario())
    kw.update(over)
    return build_to_read(**kw)


def test_lost_chats_this_week_by_fixed_rules():
    d = build()
    reasons = {r["id"]: [x["key"] for x in r["reasons"]] for r in d["lost"]}
    assert reasons == {"back": ["back"], "anyone": ["anyone"], "never": ["never"],
                       "after-yes": ["yes"], "other-team": ["back"]}
    assert "fine" not in reasons
    back = next(r for r in d["lost"] if r["id"] == "back")
    assert back["agent_name"] == "Rohan Mehta" and back["route"] == "New York → Dubai"
    assert back["client"] == "Client B."
    # Newest first.
    assert [r["id"] for r in d["lost"]][:2] == ["back", "after-yes"]
    assert d["week_start"] == "2026-11-02T05:00:00+00:00"


def test_the_sample_is_last_weeks_two_per_person_stable_and_without_lost_chats():
    d = build()
    by_person = {}
    for r in d["sample"]:
        by_person.setdefault(r["agent_name"], []).append(r)
    victor = [r["id"] for r in by_person["Victor Rusu"]]
    assert len(victor) == 2 and all(i.startswith("v-") for i in victor)
    rohan = by_person["Rohan Mehta"]
    assert all(not r["id"].startswith("r-lost") for r in rohan)
    # Rohan's week: replies of 80 s, and 2 of 6 chats back to the assistant —
    # past the help line, so one more.
    assert sorted(r["kind"] for r in rohan) == ["help", "sample", "sample"]
    # Stable: a new chat this week doesn't change last week's pick.
    s = scenario()
    s["activity"].append({"user_id": VICTOR, "conversation_id": "brand-new", "action": "claim_won", "happened_at": at(minutes=5)})
    s["conversations"].append(conv("brand-new"))
    again = build_to_read(now=NOW, offset_minutes=OFFSET, **s)
    assert {r["id"] for r in again["sample"]} == {r["id"] for r in d["sample"]}


def test_the_sample_is_the_same_week_for_readers_in_any_time_zone():
    """The pick is a hash of the reader's week, the person and the chat: the
    owner in Chisinau (+120) and a supervisor in New York (-300) read the same
    sample. Monday 00:00 at +120 is Sunday 22:00 UTC, the ISO week before."""
    east = build_to_read(now=NOW, offset_minutes=120, **scenario())
    west = build_to_read(now=NOW, offset_minutes=-300, **scenario())
    assert east["sample_week"]["start"] == "2026-10-25T22:00:00+00:00"
    assert west["sample_week"]["start"] == "2026-10-26T05:00:00+00:00"
    pick = lambda d: sorted((r["agent_id"], r["id"], r["kind"]) for r in d["sample"])  # noqa: E731
    assert pick(east) == pick(west)
    assert pick(east)


def test_a_supervisor_sees_only_their_team_masked():
    d = build(team_ids={"team-1"}, masked=True)
    ids = {r["id"] for r in d["lost"] + d["sample"]}
    assert "other-team" not in ids and "back" in ids
    assert all(r["client"].startswith("•••") for r in d["lost"])
    assert build(team_ids=set())["lost"] == []


# ── One chat ─────────────────────────────────────────────────────────


def reading_inputs(**over):
    created = NOW - timedelta(minutes=40)
    take_at = created + timedelta(minutes=2)
    d = dict(
        conv=conv("c1", created_at=created.isoformat(), closed_at=(created + timedelta(minutes=30)).isoformat(),
                  visitor_name="Olga Vasilescu", visitor_phone="+1 555 012 3377", visitor_email="olga.v@example.com",
                  closed_by_agent_id=None),
        lead={"id": "L1", "conversation_id": "c1", "origin_code": "JFK", "destination_code": "DXB",
              "route_display": "New York → Dubai", "departure_date": "2026-11-20", "return_date": "2026-11-27",
              "passengers": 1, "trip_type": "round_trip", "cabin_class": "business", "created_in_crm": True,
              "crm_lead_id": "208131", "crm_push_gate_reason": None},
        messages=[
            {"id": "m1", "role": "user", "content": "New York to Dubai", "created_at": (created + timedelta(minutes=1)).isoformat()},
            {"id": "m2", "role": "agent", "content": "Hi Olga, I'm Rohan.", "created_at": (take_at + timedelta(seconds=41)).isoformat()},
            {"id": "m3", "role": "user", "content": "Mail me at olga.v@example.com", "created_at": (take_at + timedelta(minutes=2)).isoformat()},
            {"id": "m4", "role": "user", "content": "Hello? Are you still there?", "created_at": (take_at + timedelta(minutes=20)).isoformat()},
            {"id": "m5", "role": "system", "content": "Sorry for the wait — I'm here.", "created_at": (take_at + timedelta(minutes=22)).isoformat()},
        ],
        activity=[
            {"user_id": ROHAN, "conversation_id": "c1", "action": "claim_won", "happened_at": take_at.isoformat()},
            {"user_id": ROHAN, "conversation_id": "c1", "action": "deadline_fired",
             "happened_at": (take_at + timedelta(minutes=22)).isoformat()},
        ],
        ready_log=[{"user_id": ROHAN, "is_ready": False, "changed_at": (take_at + timedelta(minutes=24)).isoformat()}],
        agent_week=[
            {"user_id": ROHAN, "conversation_id": "c1", "action": "claim_won", "happened_at": take_at.isoformat()},
            {"user_id": ROHAN, "conversation_id": "c1", "action": "deadline_fired", "happened_at": (take_at + timedelta(minutes=22)).isoformat()},
            {"user_id": ROHAN, "conversation_id": "c2", "action": "claim_won", "happened_at": at(days=1)},
        ],
        users=USERS,
    )
    d.update(over)
    return d


def test_reading_one_chat():
    r = build_reading(now=NOW, offset_minutes=OFFSET, masked=False, **reading_inputs())
    assert r["client"] == "Olga V." and r["person"]["name"] == "Rohan Mehta"
    agent = next(m for m in r["messages"] if m["role"] == "agent")
    assert agent["name"] == "Rohan Mehta"
    kinds = [e["kind"] for e in r["events"]]
    assert kinds == ["started", "taken", "first_reply", "went_back", "not_ready", "ended"]
    assert next(e for e in r["events"] if e["kind"] == "first_reply")["seconds"] == 41
    assert [x["key"] for x in r["reasons"]] == ["back", "anyone"]
    assert r["lead"]["route"] == "New York → Dubai" and r["lead"]["crm_state"] == "in_crm"
    assert r["lead"]["email"] == "olga.v@example.com"
    assert r["week"]["chats"] == 2 and r["week"]["went_back"] == 1


def test_a_supervisor_reads_it_masked_in_the_messages_too():
    r = build_reading(now=NOW, offset_minutes=OFFSET, masked=True, **reading_inputs())
    assert r["client"] == "•••cu"
    assert r["lead"]["email"] == "ol•••@example.com" and r["lead"]["phone"] == "•••3377"
    assert all("olga.v@example.com" not in (m["text"] or "") for m in r["messages"])


# ── The endpoints ────────────────────────────────────────────────────


def _client(role: str, uid: str = "sup-1") -> TestClient:
    from app.api.conversations import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": uid, "role": role, "tunnel_scope": "sales"}
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_cache():
    from app.api import conversations

    conversations._TO_READ_CACHE.clear()
    yield
    conversations._TO_READ_CACHE.clear()


@pytest.mark.parametrize("role", ["sales", "support", "qa", "project_manager"])
def test_to_read_is_for_supervisors_and_the_owner(role):
    assert _client(role).get("/api/conversations/to-read").status_code == 403
    assert _client(role).get("/api/conversations/c1/reading").status_code == 403


def test_a_supervisor_without_a_team_gets_an_empty_list_not_everything():
    read = AsyncMock(return_value=scenario())
    with patch("app.db.supabase.get_to_read_inputs", new=read), \
         patch("app.db.supabase.get_team_ids_for_supervisor", new=AsyncMock(return_value=[])):
        body = _client("supervisor").get("/api/conversations/to-read").json()
    assert body["data"]["no_team"] is True and body["data"]["lost"] == [] and read.await_count == 0


def test_the_owner_reads_every_chat_and_the_read_is_shared():
    read = AsyncMock(return_value=scenario())
    with patch("app.db.supabase.get_to_read_inputs", new=read):
        a = _client("owner", "own-1").get("/api/conversations/to-read?tz_offset_minutes=-300")
        b = _client("admin", "adm-1").get("/api/conversations/to-read?tz_offset_minutes=-300")
    assert a.status_code == b.status_code == 200
    assert read.await_count == 1
    assert a.json()["data"]["no_team"] is False


def test_a_failed_read_is_a_502():
    with patch("app.db.supabase.get_to_read_inputs", new=AsyncMock(side_effect=RuntimeError("boom"))):
        assert _client("owner", "own-1").get("/api/conversations/to-read").status_code == 502
    with patch("app.db.supabase.get_reading_inputs", new=AsyncMock(side_effect=RuntimeError("boom"))):
        assert _client("owner", "own-1").get("/api/conversations/c1/reading").status_code == 502


def test_reading_another_teams_chat_is_403_and_a_missing_one_404():
    with patch("app.db.supabase.get_reading_inputs", new=AsyncMock(return_value=reading_inputs())), \
         patch("app.db.supabase.get_team_ids_for_supervisor", new=AsyncMock(return_value=["team-2"])):
        assert _client("supervisor").get("/api/conversations/c1/reading").status_code == 403
    with patch("app.db.supabase.get_reading_inputs", new=AsyncMock(return_value=reading_inputs())), \
         patch("app.db.supabase.get_team_ids_for_supervisor", new=AsyncMock(return_value=["team-1"])):
        body = _client("supervisor").get("/api/conversations/c1/reading").json()
    assert body["data"]["client"] == "•••cu"
    with patch("app.db.supabase.get_reading_inputs", new=AsyncMock(return_value=None)):
        assert _client("owner", "own-1").get("/api/conversations/nope/reading").status_code == 404
