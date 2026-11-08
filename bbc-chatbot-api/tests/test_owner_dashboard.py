"""The owner's Dashboard — every number from data the server already keeps.

What is pinned here:
  * "what the AI had just asked" is a fixed-rule reading of the last
    assistant message, checked on the phrasings production actually sends;
  * Ready to quote is cumulative: route, a date that wasn't already past,
    travelers and the return, the client's YES, the CRM accepting the lead;
  * a take lasts until the next take, the deadline or the close, and the
    first reply is the first operator message inside it;
  * the previous period has the same length (today so far vs yesterday up
    to the same time);
  * Needs attention is shown only past fixed lines;
  * only owner, admin and dev read it, a failed read is a 502, and the
    result is shared for a minute;
  * supervisors, project managers and QA read the same build cut to their
    tunnel, without the AI's cost or the sources, supervisors with masked
    names, and chat links only for the chats of their own teams.
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
from app.services.owner_dashboard import (  # noqa: E402
    build_owner_dashboard,
    build_takes,
    classify_last_ai,
    device_of,
    fetch_since,
    mentions_phone,
    page_of,
    ready_intervals,
    ready_steps,
    source_of,
)

NOW = datetime(2026, 11, 6, 17, 0, tzinfo=timezone.utc)  # 12:00 in New York (-300)
OFFSET = -300
ROHAN, VICTOR = "agent-rohan", "agent-victor"
USERS = {
    ROHAN: {"id": ROHAN, "name": "Rohan Mehta", "role": "sales", "is_active": True},
    VICTOR: {"id": VICTOR, "name": "Victor Rusu", "role": "sales", "is_active": True},
    "qa-1": {"id": "qa-1", "name": "Elena Vasile", "role": "qa", "is_active": True},
}


def at(hours_ago: float = 0, minutes_ago: float = 0, seconds_ago: float = 0) -> str:
    return (NOW - timedelta(hours=hours_ago, minutes=minutes_ago, seconds=seconds_ago)).isoformat()


def conv(cid: str, hours_ago: float, **over) -> dict:
    row = {
        "id": cid, "chat_number": 100, "tunnel": "sales", "mode": "ai", "status": "closed",
        "created_at": at(hours_ago), "visitor_name": f"Client {cid}", "has_phone": True,
        "confirmed_at": None, "site": "bbc", "utm_source": None, "page_url": None,
        "user_agent": None, "closed_by_agent_id": None,
    }
    row.update(over)
    return row


def lead(cid: str, **over) -> dict:
    row = {
        "conversation_id": cid, "created_at": at(1), "origin_code": "JFK", "destination_code": "LHR",
        "departure_date": "2026-12-01", "return_date": "2026-12-10", "passengers": 2,
        "trip_type": "round_trip", "created_in_crm": True, "crm_push_gate_reason": None,
        "reviewed_by_qa": False,
    }
    row.update(over)
    return row


def build(**over) -> dict:
    base = dict(
        now=NOW, offset_minutes=OFFSET, days=7, conversations=[], leads={}, activity=[],
        user_msgs=[], agent_msgs=[], ai_msgs=[], runs=[], ready_log=[], users=USERS,
        last_review_at="2026-08-22T11:33:37+00:00",
    )
    base.update(over)
    return build_owner_dashboard(**base)


# ── Reading the assistant's last message ─────────────────────────────


@pytest.mark.parametrize("text,key", [
    ("Barcelona it is. Where will you be flying from?", "from"),
    ("Colombo — a long-haul route worth doing right. Where will you be flying from?", "from"),
    ("End of January to Bangkok with flexible dates. Which US city are you departing from?", "from"),
    ("Kathy, welcome! You're in the right place for business class. Where are you flying from and to?", "route"),
    ("Hi Christine! Where are you flying to?", "to"),
    ("New York as the origin — and where are you headed?", "to"),
    ("LAX to London — when are you looking to depart?", "dates"),
    ("Round trip it is — when would you fly back?", "dates"),
    ("Looks like that may have been a slip — what date are you looking to fly from LAX to Manila?", "dates"),
    ("November 15th works well. Fixed date, or is there a day or two of flexibility?", "flexible"),
    ("Dallas to Rome, May 19–28 — a solid window. How many travelers will be on this trip?", "travelers"),
    ("Norfolk to Santorini, August 8–16. What's the occasion — a special getaway, or something else?", "occasion"),
    ("Let me confirm your request: ✈ JFK → MAD 👥 1 passenger(s) Is everything correct? Reply YES to confirm.", "summary"),
    ("Let me get a consultant on this with you — they'll sort the details in a minute. What's the best number to reach you?", "specialist"),
    ("Absolutely — I'm connecting you with a specialist right now.", "specialist"),
    ("Of course — I'm putting a consultant on this to call you on +17167137896.", "callback"),
    ("Thanks, Ida — your consultant takes it from here. Speak soon.", "goodbye"),
    ("Of course — you can reach our team directly at +1 (888) 322-7999 and they'll pull up your history.", "phone"),
    ("For immediate help, call +1 (888) 668-3009.", "phone"),
    ("I'll save those details for you.", "other"),
    ("", "other"),
    (None, "other"),
])
def test_what_the_ai_had_just_asked(text, key):
    assert classify_last_ai(text) == key


def test_a_clients_own_number_is_not_our_phone_line():
    assert not mentions_phone("I'm putting a consultant on this to call you on +18886323585.")
    assert mentions_phone("call +1 888 322 7999")


def test_where_a_chat_came_from_in_words():
    assert source_of("google ") == source_of("Google") == "Google"
    assert source_of("fb") == "Facebook" and source_of("ig") == "Instagram"
    assert source_of(None) == source_of("  ") == "No source"
    assert source_of("newsletter") == "Newsletter"
    assert device_of("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)") == "Phone"
    assert device_of("Mozilla/5.0 (iPad; CPU OS 17_0)") == "Tablet"
    assert device_of("Mozilla/5.0 (Windows NT 10.0; Win64; x64)") == "Computer"
    assert device_of(None) == "Unknown"
    assert page_of("https://buybusinessclass.com/business-class-flights/london/?utm=x") == "/business-class-flights/london"
    assert page_of("https://buybusinessclass.com") == "/"


# ── Ready to quote ───────────────────────────────────────────────────


def test_ready_is_every_step_on_top_of_the_previous():
    c = conv("a", 2, confirmed_at=at(1))
    assert ready_steps(lead("a"), c) == [True, True, True, True, True]
    # A one-way trip needs no return.
    assert ready_steps(lead("a", trip_type="one_way", return_date=None), c)[2] is True
    # A round trip without a return stops at step 2, and nothing later counts.
    assert ready_steps(lead("a", return_date=None), c) == [True, True, False, False, False]
    # A departure already past when the chat started is not "a date in the future".
    assert ready_steps(lead("a", departure_date="2026-10-01"), c)[1] is False
    # Confirmed, but the CRM refused the contact: not ready.
    assert ready_steps(lead("a", created_in_crm=False), c)[3:] == [True, False]
    # No YES: stops before "confirmed".
    assert ready_steps(lead("a"), conv("a", 2))[3] is False
    assert ready_steps(None, c) == [False] * 5


# ── Takes ────────────────────────────────────────────────────────────


def test_a_take_ends_at_the_next_take_and_the_reply_must_be_inside_it():
    activity = [
        {"user_id": ROHAN, "conversation_id": "c1", "action": "claim_won", "happened_at": at(minutes_ago=30)},
        {"user_id": ROHAN, "conversation_id": "c1", "action": "deadline_fired", "happened_at": at(minutes_ago=28)},
        {"user_id": VICTOR, "conversation_id": "c1", "action": "claim_won", "happened_at": at(minutes_ago=20)},
        {"user_id": VICTOR, "conversation_id": "c1", "action": "assigned", "happened_at": at(minutes_ago=20)},
    ]
    msgs = [{"conversation_id": "c1", "created_at": at(minutes_ago=19, seconds_ago=30)}]
    takes = build_takes(activity, msgs, NOW)
    assert [(t["user_id"], t["ended_by"], t["reply_s"]) for t in takes] == [
        (ROHAN, "deadline_fired", None),   # Victor's message is not Rohan's reply
        (VICTOR, None, 30),                # claim_won + assigned for one claim = one take
    ]


def test_ready_hours_are_clipped_to_the_period():
    rows = [
        {"user_id": ROHAN, "is_ready": True, "changed_at": at(30)},
        {"user_id": ROHAN, "is_ready": False, "changed_at": at(20)},
        {"user_id": ROHAN, "is_ready": True, "changed_at": at(2)},
        # A "not ready" with no "ready" before it in the read starts nothing:
        # it is a repeated "not ready", not the end of a stretch.
        {"user_id": VICTOR, "is_ready": False, "changed_at": at(1)},
    ]
    spans = ready_intervals(rows, NOW - timedelta(hours=24), NOW)
    hours = {u: sum((b - a).total_seconds() for a, b in s) / 3600 for u, s in spans.items()}
    assert hours == {ROHAN: 4 + 2}


# ── The page ─────────────────────────────────────────────────────────


def test_the_previous_period_has_the_same_length():
    d = build(days=1)
    assert d["range_start"] == "2026-11-06T05:00:00+00:00"   # midnight in New York
    assert d["previous"] == {"start": "2026-11-05T05:00:00+00:00", "end": "2026-11-05T17:00:00+00:00"}
    # "Today" still draws a week of bars.
    assert len(d["overview"]["by_day"]) == 7
    assert fetch_since(NOW, OFFSET, 1) == datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc)
    assert fetch_since(NOW, OFFSET, 30) == datetime(2026, 9, 8, 5, 0, tzinfo=timezone.utc)


def _scenario() -> dict:
    convs = [
        # Ready, only the AI.
        conv("ready-ai", 30, confirmed_at=at(29.9), status="completed", utm_source="google "),
        # Ready to the AI, then taken by Rohan: came in after a YES.
        conv("ready-then-agent", 28, confirmed_at=at(27.9), status="completed", utm_source="Google"),
        # Contact only: the form, no message.
        conv("contact", 26),
        # Only the AI, left after "where are you flying from".
        conv("left-from", 25, utm_source="kayak"),
        # Only the AI, still open: not a leaver yet.
        conv("open-ai", 1, status="active"),
        # With an agent: Rohan never answered, it went back.
        conv("back-1", 20), conv("back-2", 19), conv("back-3", 18),
        # With an agent: Rohan answered after 80 s, closed without sending to the CRM.
        conv("slow", 17, closed_by_agent_id=ROHAN),
        # Victor answered after 20 s.
        conv("fast", 16),
        # In the previous period: one leaver after "where from".
        conv("old-left", 24 * 8, utm_source="kayak"),
    ]
    leads = {
        "ready-ai": lead("ready-ai", reviewed_by_qa=False, created_at=at(72)),
        "ready-then-agent": lead("ready-then-agent", reviewed_by_qa=True),
        "contact": lead("contact", origin_code=None, destination_code=None, departure_date=None),
        "left-from": lead("left-from", origin_code=None),
        "open-ai": lead("open-ai"),
        "back-1": lead("back-1"), "back-2": lead("back-2"), "back-3": lead("back-3"),
        "slow": lead("slow", created_in_crm=False),
        "fast": lead("fast", created_in_crm=False, crm_push_gate_reason="email_typo_domain"),
        "old-left": lead("old-left", origin_code=None),
    }
    wrote = [c["id"] for c in convs if c["id"] != "contact"]
    user_msgs = [{"conversation_id": cid, "created_at": at(0.5)} for cid in wrote]
    activity, agent_msgs = [], []

    def take(cid, who, hours_ago, end=None, reply_s=None):
        activity.append({"user_id": who, "conversation_id": cid, "action": "claim_won", "happened_at": at(hours_ago)})
        if reply_s is not None:
            agent_msgs.append({"conversation_id": cid, "created_at": (NOW - timedelta(hours=hours_ago) + timedelta(seconds=reply_s)).isoformat()})
        if end:
            activity.append({"user_id": who, "conversation_id": cid, "action": end,
                             "happened_at": (NOW - timedelta(hours=hours_ago) + timedelta(seconds=95)).isoformat()})

    take("ready-then-agent", ROHAN, 27, end="closed", reply_s=70)
    take("back-1", ROHAN, 20, end="deadline_fired")
    take("back-2", ROHAN, 19, end="deadline_fired")
    take("back-3", ROHAN, 18, end="deadline_fired")
    take("slow", ROHAN, 17, end="closed", reply_s=80)
    take("fast", VICTOR, 16, end="closed", reply_s=20)

    ai_msgs = [
        {"id": "m1", "conversation_id": "left-from", "content": "London it is! Where will you be flying from?", "created_at": at(24.9)},
        {"id": "m2", "conversation_id": "old-left", "content": "Rome it is! Where will you be flying from?", "created_at": at(24 * 8 - 0.1)},
        {"id": "m3", "conversation_id": "ready-ai", "content": "Your consultant prices that directly.", "created_at": at(29.95)},
        {"id": "m4", "conversation_id": "ready-ai", "content": "Your consultant prices that directly.", "created_at": at(29.94)},
    ]
    runs = [
        {"conversation_id": "ready-ai", "message_id": "m3", "cost": 0.5, "latency_ms": 6000, "had_fallback": False,
         "intent_detected": "price_inquiry", "created_at": at(29.95)},
        {"conversation_id": "left-from", "message_id": "m1", "cost": 0.25, "latency_ms": 5500, "had_fallback": True,
         "intent_detected": "general_question", "created_at": at(24.9)},
    ]
    ready_log = [
        {"user_id": ROHAN, "is_ready": True, "changed_at": at(21)},
        {"user_id": ROHAN, "is_ready": False, "changed_at": at(15)},
    ]
    return dict(conversations=convs, leads=leads, activity=activity, user_msgs=user_msgs,
                agent_msgs=agent_msgs, ai_msgs=ai_msgs, runs=runs, ready_log=ready_log)


def test_overview_counts_every_chat_once():
    d = build(**_scenario())
    now = d["overview"]["now"]
    assert now["started"] == 10 and now["contact_only"] == 1 and now["wrote"] == 9
    assert now["ready"] == 2 and now["ready_ai_only"] == 1 and now["ready_with_agent"] == 1
    assert now["ai_only"] == 3 and now["ai_only_ready"] == 1      # ready-ai, left-from, open-ai
    assert now["with_agent"] == 6
    assert d["overview"]["before"]["started"] == 1
    assert sum(x["ready"] + x["other"] + x["contact_only"] for x in d["overview"]["by_day"]) == 10


def test_funnel_and_where_people_leave():
    d = build(**_scenario())
    # ready-ai and open-ai have every trip step; left-from has no route.
    assert d["funnel"]["ai_only"] == [2, 2, 2, 1, 1]
    assert d["funnel"]["time_to_ready_median_s"] == 360
    leave = d["leave"]
    assert leave["total"] == 1 and leave["total_before"] == 1
    assert [(r["key"], r["chats"], r["before"]) for r in leave["rows"]] == [("from", 1, 1)]
    assert leave["rows"][0]["conversations"][0]["first_name"] == "Client"


def test_sources_count_google_spellings_as_one():
    d = build(**_scenario())
    google = next(r for r in d["sources"]["source"] if r["name"] == "Google")
    assert (google["chats"], google["ready"]) == (2, 2)
    assert d["source_spellings"] == ["google "]


def test_agents_block():
    d = build(**_scenario())
    a = d["agents"]["now"]
    assert a["given"] == 6 and a["went_back"] == 3 and a["after_yes"] == 1 and a["never_wrote"] == 0
    assert a["first_reply_median_s"] == 70 and a["replies_over_deadline"] == 0
    rohan = next(r for r in d["agents"]["by_agent"] if r["user_id"] == ROHAN)
    assert rohan["chats"] == 5 and rohan["went_back"] == 3 and rohan["after_yes"] == 1
    assert rohan["first_reply_median_s"] == 75 and rohan["ready_hours"] == 6.0
    # Answered, has a phone, never reached the CRM (closing doesn't send it).
    assert rohan["not_in_crm"] == 1
    victor = next(r for r in d["agents"]["by_agent"] if r["user_id"] == VICTOR)
    assert victor["not_in_crm"] == 1 and victor["first_reply_median_s"] == 20


def test_needs_attention_only_past_the_lines():
    d = build(**_scenario())
    kinds = {a["kind"]: a for a in d["attention"]}
    # Rohan: 3 of 5 back, first reply 75 s > 60 s, at least 3 chats.
    assert kinds["agent"]["name"] == "Rohan Mehta" and kinds["agent"]["went_back"] == 3
    # One went back per hour: under the line of 3.
    assert "hour" not in kinds
    # The ready lead created 3 days ago waits for review past 2 days.
    assert kinds["review"]["waiting_long"] == 1
    # Typical answer time 5.75 s is over 5 s.
    assert kinds["ai_latency"]["latency_ms"] == 5750
    victor_only = build(**{**_scenario(), "activity": [
        a for a in _scenario()["activity"] if a["user_id"] == VICTOR]})
    assert "agent" not in {a["kind"] for a in victor_only["attention"]}


def test_quality_block():
    d = build(**_scenario())
    q = d["quality"]
    assert (q["ready"], q["reviewed"], q["to_review"]) == (2, 1, 1)
    assert q["qa_accounts"] == 1 and q["last_review_at"].startswith("2026-08-22")
    assert q["ai"]["cost"] == 0.75 and q["ai"]["cost_per_ready"] == 0.375
    assert q["ai"]["template_answers"] == 1 and q["ai"]["answers"] == 2
    review = {r["key"]: r for r in q["ai_review"]}
    assert review["price"]["chats"] == 1 and review["price"]["left_after"] == 0
    assert review["repeat"]["chats"] == 1
    crm = q["crm"]
    assert crm["reached"] == 8 and crm["confirmed"] == 2
    assert crm["from_agent_chats"] == 3 and crm["defaults"] == 3
    assert crm["not_in_crm"] == 2 and crm["closed_by_agent"] == 1 and crm["refused"] == 1
    assert crm["refused_reasons"] == {"email_typo_domain": 1}


# ── The endpoint ─────────────────────────────────────────────────────


def _client(role: str) -> TestClient:
    from app.api.dashboard import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "role": role, "tunnel_scope": "all"}
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_cache():
    from app.api import dashboard

    dashboard._INPUTS_CACHE.clear()
    dashboard._BUILD_CACHE.clear()
    yield
    dashboard._INPUTS_CACHE.clear()
    dashboard._BUILD_CACHE.clear()


EMPTY_INPUTS = dict(conversations=[], leads={}, activity=[], user_msgs=[], agent_msgs=[], ai_msgs=[],
                    runs=[], ready_log=[], users={}, last_review_at=None)


@pytest.mark.parametrize("role", ["sales", "support", "supervisor", "project_manager", "qa"])
def test_only_owner_admin_and_dev_read_the_owners(role):
    assert _client(role).get("/api/dashboard/owner?days=7").status_code == 403


def test_bad_range_is_422():
    assert _client("owner").get("/api/dashboard/owner?days=14").status_code == 422


def test_a_failed_read_is_a_502_not_a_page_of_zeros():
    with patch("app.db.supabase.get_owner_dashboard_inputs", new=AsyncMock(side_effect=RuntimeError("boom"))):
        r = _client("admin").get("/api/dashboard/owner?days=7&tz_offset_minutes=-300")
    assert r.status_code == 502


def test_one_read_a_minute_is_shared():
    read = AsyncMock(return_value=EMPTY_INPUTS)
    with patch("app.db.supabase.get_owner_dashboard_inputs", new=read):
        c = _client("dev")
        a = c.get("/api/dashboard/owner?days=30&tz_offset_minutes=120")
        b = c.get("/api/dashboard/owner?days=30&tz_offset_minutes=120")
        c.get("/api/dashboard/owner?days=7&tz_offset_minutes=120")
    assert a.status_code == b.status_code == 200
    assert a.json()["data"]["days"] == 30 and a.json()["success"] is True
    assert read.await_count == 2


# ── Supervisor, project manager and QA ───────────────────────────────


def _two_tunnels() -> dict:
    support_agent = {"id": "agent-priya", "name": "Priya Nair", "role": "support", "tunnel_scope": "support", "is_active": True}
    users = {**USERS, support_agent["id"]: support_agent}
    s = _scenario()
    s["conversations"] = [
        *s["conversations"],
        conv("support-1", 3, tunnel="support", visitor_name="Laura Bennett", team_id="team-support"),
    ]
    s["user_msgs"] = [*s["user_msgs"], {"conversation_id": "support-1", "created_at": at(2.5)}]
    s["activity"] = [*s["activity"], {"user_id": "agent-priya", "conversation_id": "support-1", "action": "claim_won", "happened_at": at(2.4)}]
    # Priya answered and closed it without sending: a "Not in the CRM" link.
    s["agent_msgs"] = [*s["agent_msgs"], {"conversation_id": "support-1", "created_at": at(2.39)}]
    s["leads"] = {**s["leads"], "support-1": lead("support-1", created_in_crm=False)}
    s["ready_log"] = [
        {"user_id": ROHAN, "is_ready": True, "changed_at": at(5)},
        {"user_id": "agent-priya", "is_ready": True, "changed_at": at(5)},
    ]
    s["users"] = users
    s["last_review_at"] = None
    return s


def test_a_tunnel_scope_keeps_only_that_tunnels_chats_and_people():
    from app.services.owner_dashboard import scope_inputs

    s = _two_tunnels()
    sales = scope_inputs(s, "sales")
    assert "support-1" not in {c["id"] for c in sales["conversations"]}
    assert all(a["conversation_id"] != "support-1" for a in sales["activity"])
    # Ready hours: only people who work sales.
    assert {r["user_id"] for r in sales["ready_log"]} == {ROHAN}
    support = scope_inputs(s, "support")
    assert {c["id"] for c in support["conversations"]} == {"support-1"}
    assert {r["user_id"] for r in support["ready_log"]} == {"agent-priya"}
    # No tunnel (QA, a supervisor of both): everything.
    assert scope_inputs(s, None) is s and scope_inputs(s, "all") is s
    names = {a["name"] for a in build(now=NOW, **{k: v for k, v in support.items()})["agents"]["by_agent"]}
    assert names == {"Priya Nair"}


def _refs(node) -> list[dict]:
    out = []
    if isinstance(node, dict):
        if {"id", "chat_number", "first_name"} <= set(node) and len(node) <= 5:
            out.append(node)
        for v in node.values():
            out += _refs(v)
    elif isinstance(node, list):
        for v in node:
            out += _refs(v)
    return out


def test_redaction_never_touches_the_shared_build():
    import copy

    from app.services.owner_dashboard import redact

    d = build(**{k: v for k, v in _two_tunnels().items() if k != "users"}, users=_two_tunnels()["users"])
    before = copy.deepcopy(d)
    out = redact(d, mask_names=True, show_cost=False, show_sources=False, team_ids={"team-support"})
    assert d == before
    assert out["quality"]["ai"] is None and out["sources"] is None and out["sites"] == []
    assert all("team_id" not in r for r in _refs(out))
    # Only chats of the viewer's teams stay as links; the counts don't move.
    assert {r["id"] for r in _refs(out)} <= {"support-1"}
    assert out["agents"]["by_agent"] == [
        {**a, "not_in_crm_conversations": [r for r in a["not_in_crm_conversations"] if r["id"] == "support-1"]}
        for a in out["agents"]["by_agent"]
    ]
    owner = redact(d, mask_names=False, show_cost=True, show_sources=True)
    assert owner["quality"]["ai"] == d["quality"]["ai"] and all("team_id" not in r for r in _refs(owner))
    assert len(_refs(owner)) == len(_refs(d))
    assert any(r["first_name"] == "Client" for r in _refs(owner))
    assert all(set(r) == {"id", "first_name", "chat_number"} for r in _refs(owner) + _refs(out))


def test_supervisors_see_masked_first_names():
    from app.services.owner_dashboard import redact

    d = build(**_scenario())
    out = redact(d, mask_names=True, show_cost=False, show_sources=False, team_ids=None)
    names = {r["first_name"] for r in _refs(out)}
    # The whole name masked, as in Conversations: "Client slow" → "•••ow".
    assert names and all(n.startswith("•••") for n in names) and "•••ow" in names


@pytest.fixture
def at_fixture_time():
    """The endpoint reads the real clock; these fixtures are dated NOW. Pin
    the endpoint's clock to NOW, so the tests still mean the same thing in
    CI's run 120 days ahead (tests/plugins/timetravel.py)."""

    class _FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    with patch("app.api.dashboard.datetime", _FixedNow):
        yield


def _team_client(role: str, tunnel_scope: str, uid: str = "sup-1") -> TestClient:
    from app.api.dashboard import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": uid, "role": role, "tunnel_scope": tunnel_scope}
    return TestClient(app)


@pytest.mark.parametrize("role", ["sales", "support", "owner", "admin", "dev"])
def test_the_team_dashboard_is_for_supervisors_pms_and_qa(role):
    assert _team_client(role, "all").get("/api/dashboard/team?days=7").status_code == 403


def test_a_sales_supervisor_reads_all_sales_chats_with_links_only_to_their_team(at_fixture_time):
    s = _two_tunnels()
    s["conversations"] = [
        {**c, "team_id": "team-sales"} if c["id"] == "slow" else c for c in s["conversations"]
    ]
    read = AsyncMock(return_value=s)
    with patch("app.db.supabase.get_owner_dashboard_inputs", new=read), \
         patch("app.db.supabase.get_team_ids_for_supervisor", new=AsyncMock(return_value=["team-sales"])):
        r = _team_client("supervisor", "sales").get("/api/dashboard/team?days=7&tz_offset_minutes=-300")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["scope"] == {"tunnel": "sales", "team_scoped_links": True}
    assert "Priya Nair" not in {a["name"] for a in d["agents"]["by_agent"]}
    assert d["quality"]["ai"] is None and d["sources"] is None
    assert {x["id"] for x in _refs(d)} == {"slow"}
    assert all(x["first_name"].startswith("•••") for x in _refs(d))


def test_a_supervisor_without_a_team_still_gets_the_numbers_but_no_links(at_fixture_time):
    with patch("app.db.supabase.get_owner_dashboard_inputs", new=AsyncMock(return_value=_two_tunnels())), \
         patch("app.db.supabase.get_team_ids_for_supervisor", new=AsyncMock(return_value=[])):
        d = _team_client("supervisor", "sales").get("/api/dashboard/team?days=7").json()["data"]
    assert d["overview"]["now"]["started"] > 0
    assert _refs(d) == []


def test_qa_reads_every_chat_unmasked_and_without_cost(at_fixture_time):
    with patch("app.db.supabase.get_owner_dashboard_inputs", new=AsyncMock(return_value=_two_tunnels())):
        d = _team_client("qa", "all", uid="qa-1").get("/api/dashboard/team?days=7").json()["data"]
    assert d["scope"] == {"tunnel": "all", "team_scoped_links": False}
    assert "Priya Nair" in {a["name"] for a in d["agents"]["by_agent"]}
    assert d["quality"]["ai"] is None and d["quality"]["crm"]["reached"] >= 1
    assert any(x["first_name"] == "Laura" for x in _refs(d))
    assert all(a["kind"] != "ai_latency" for a in d["attention"])


def test_a_project_manager_sees_names_and_their_teams_links(at_fixture_time):
    with patch("app.db.supabase.get_owner_dashboard_inputs", new=AsyncMock(return_value=_two_tunnels())), \
         patch("app.db.supabase.get_team_ids_for_pm", new=AsyncMock(return_value=["team-support"])):
        d = _team_client("project_manager", "all", uid="pm-1").get("/api/dashboard/team?days=7").json()["data"]
    assert d["scope"]["tunnel"] == "all"
    # The clock is pinned, so the week has chats: exactly their team's, unmasked.
    assert {(x["id"], x["first_name"]) for x in _refs(d)} == {("support-1", "Laura")}


def test_owner_and_team_dashboards_share_one_read():
    read = AsyncMock(return_value=_two_tunnels())
    with patch("app.db.supabase.get_owner_dashboard_inputs", new=read), \
         patch("app.db.supabase.get_team_ids_for_supervisor", new=AsyncMock(return_value=[])):
        _client("owner").get("/api/dashboard/owner?days=7&tz_offset_minutes=0")
        _team_client("supervisor", "sales").get("/api/dashboard/team?days=7&tz_offset_minutes=0")
        _team_client("qa", "all", uid="qa-1").get("/api/dashboard/team?days=7&tz_offset_minutes=0")
    assert read.await_count == 1


def test_expired_reads_leave_memory():
    import time as _time

    from app.api import dashboard

    dashboard._INPUTS_CACHE[(7, 60)] = (_time.monotonic() - 3600, NOW, {})
    with patch("app.db.supabase.get_owner_dashboard_inputs", new=AsyncMock(return_value=EMPTY_INPUTS)):
        _client("owner").get("/api/dashboard/owner?days=7&tz_offset_minutes=0")
    assert (7, 60) not in dashboard._INPUTS_CACHE
