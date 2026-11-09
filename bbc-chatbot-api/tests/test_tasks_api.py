"""Tasks: who sees which tasks, what each role may change, what happens before
migration 051 runs, and the tasks that make themselves (a promise in an
agent's message, a client's "call me", someone going Not ready).
"""

import asyncio
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

from app.api import notifications, tasks  # noqa: E402
from app.db import supabase as db  # noqa: E402
from app.security.auth import get_current_user  # noqa: E402
from app.services import tasks_service  # noqa: E402
from app.services.follow_ups import rules_of  # noqa: E402
from app.services.tasks_view import build_tasks_view  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)
NAMES = {"victor": "Victor Rusu", "rohan": "Rohan Mehta", "irina": "Irina Moraru", "alex": "Alex Moore"}
PEOPLE = [{"id": k, "name": v, "role": "sales"} for k, v in NAMES.items()]


def task(tid: str, **over) -> dict:
    base = {
        "id": tid, "task_number": 1, "title": "Send Michael three options for New York → London", "kind": "email",
        "source": "caught", "conversation_id": "c1", "lead_id": "l1", "message_id": f"m-{tid}",
        "quote": "I’ll send you three options within the hour. Write to michael.r@example.com",
        "due_at": (NOW + timedelta(hours=1)).isoformat(), "due_kind": "said", "assignee_id": "victor",
        "created_by": "victor", "unowned_reason": None, "unowned_from": None, "unowned_at": None,
        "done_at": None, "done_by": None, "dismissed_at": None, "dismissed_by": None,
        "created_at": (NOW - timedelta(minutes=5)).isoformat(), "updated_at": None,
    }
    base.update(over)
    return base


CONVS = {"c1": {"id": "c1", "chat_number": 4810, "visitor_name": "Michael Reed", "visitor_email": "michael.r@example.com",
                "visitor_phone": "+1 555 014 2290", "status": "active", "mode": "human", "assigned_agent_id": "victor"}}
LEADS = {"c1": {"id": "l1", "conversation_id": "c1", "route_display": "New York → London", "origin_code": "ORD",
                "departure_date": "2026-11-16", "passengers": 2, "created_in_crm": True, "crm_lead_id": "208131"}}


def client(role: str, uid: str) -> TestClient:
    app = FastAPI()
    app.include_router(tasks.router, prefix="/api")
    app.include_router(notifications.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": uid, "role": role}
    return TestClient(app)


def patched(**extra):
    """The DB around the tasks endpoints, with the rules cache cleared."""
    tasks_service.forget_rules()
    base = {
        "get_task_rules_raw": AsyncMock(return_value=None),
        "list_follow_ups": AsyncMock(return_value=([task("t1")], [])),
        "get_follow_up_context": AsyncMock(return_value=(CONVS, LEADS)),
        "client_messages_since": AsyncMock(return_value=[]),
        "get_user_names": AsyncMock(return_value=NAMES),
        "get_task_people": AsyncMock(return_value=PEOPLE),
        "get_team_ids_for_supervisor": AsyncMock(return_value=["team-1"]),
        "get_operator_ids_for_teams": AsyncMock(return_value=["victor", "rohan"]),
        "get_follow_up": AsyncMock(return_value=task("t1")),
        "update_follow_up": AsyncMock(side_effect=lambda tid, p: {**task(tid), **p}),
        "insert_follow_up": AsyncMock(side_effect=lambda row: {"id": "new", **row}),
        "add_task_event": AsyncMock(return_value=None),
        "get_task_events": AsyncMock(return_value=[]),
        "messages_around": AsyncMock(return_value=[]),
        "get_pending_conversations": AsyncMock(return_value=[{"id": "waiting"}]),
    }
    base.update(extra)
    return patch.multiple(db, **base)


# ── Before migration 051, and failed reads ───────────────────────────────────

def test_before_the_migration_the_page_says_so_not_an_empty_list():
    with patched(get_task_rules_raw=AsyncMock(side_effect=db.TasksNotSetUp("column app_settings.task_rules does not exist"))):
        r = client("sales", "victor").get("/api/tasks")
    assert r.status_code == 503 and "migration 051" in r.json()["detail"]


def test_a_failed_read_is_a_502():
    with patched(list_follow_ups=AsyncMock(side_effect=RuntimeError("boom"))):
        r = client("sales", "victor").get("/api/tasks")
    assert r.status_code == 502


def test_the_bell_keeps_the_waiting_chats_when_tasks_fail_or_aren_t_set_up():
    with patched(get_task_rules_raw=AsyncMock(side_effect=db.TasksNotSetUp("42703"))):
        body = client("sales", "victor").get("/api/notifications").json()
    assert body["stale_conversations"] == [{"id": "waiting"}] and body["tasks"]["set_up"] is False
    with patched(list_follow_ups=AsyncMock(side_effect=RuntimeError("boom"))):
        body = client("sales", "victor").get("/api/notifications").json()
    assert body["stale_conversations"] == [{"id": "waiting"}] and body["tasks"]["failed"] is True


def test_the_bell_carries_the_business_time_zone_and_only_today_s_tasks():
    late_one = task("t2", due_at=(NOW - timedelta(minutes=10)).isoformat())
    in_two_days = task("t3", due_at=(NOW + timedelta(days=2)).isoformat())
    with patched(list_follow_ups=AsyncMock(return_value=([late_one, in_two_days], []))):
        t = client("sales", "victor").get("/api/notifications").json()["tasks"]
    # The panel shows due times and rings reminders in this zone, not the browser's.
    assert t["time_zone"] == "America/New_York"
    assert [r["id"] for r in t["mine"]] == ["t2"] and t["counts"]["late"] == 1


def test_the_menu_number_is_the_late_tasks_and_for_a_supervisor_the_team_s_unowned_too():
    rows = [task("t1", due_at=(NOW - timedelta(minutes=5)).isoformat()),                       # Victor's, late
            task("t2", assignee_id=None, due_at=(NOW - timedelta(minutes=50)).isoformat()),    # Unowned, late
            task("t3", assignee_id="rohan", due_at=(NOW + timedelta(hours=1)).isoformat())]    # Rohan's, not yet
    with patched(list_follow_ups=AsyncMock(return_value=(rows, []))):
        lead = client("supervisor", "irina").get("/api/notifications").json()["tasks"]["counts"]
        agent = client("sales", "victor").get("/api/notifications").json()["tasks"]["counts"]
    assert lead["team_late"] == 2 and lead["unowned"] == 1
    assert agent["team_late"] == 0  # an agent gives no tasks out: their number is their own late ones


# ── Who sees what ────────────────────────────────────────────────────────────

def test_an_agent_sees_only_their_own_tasks_with_the_client_s_city():
    lf = AsyncMock(return_value=([task("t1")], []))
    with patched(list_follow_ups=lf):
        body = client("sales", "victor").get("/api/tasks?view=all").json()["data"]
    assert lf.call_args.kwargs["assignee_ids"] == ["victor"]
    row = body["tasks"][0]
    assert body["view"] == "mine" and body["can"] == {"assign": False, "rules": False}
    assert row["client"] == "Michael R." and row["client_city"] == "Chicago" and row["client_zone"] == "America/Chicago"
    assert row["quote"].endswith("michael.r@example.com") and row["state"] in ("today", "later")  # "later" past midnight in New York


def test_a_supervisor_sees_the_team_and_unowned_with_contacts_masked():
    lf = AsyncMock(return_value=([task("t1"), task("t2", assignee_id=None, unowned_reason="ai_only")], []))
    with patched(list_follow_ups=lf):
        body = client("supervisor", "irina").get("/api/tasks").json()["data"]
    kw = lf.call_args.kwargs
    assert kw["assignee_ids"] == ["irina", "rohan", "victor"] and kw["with_unowned"] is True
    row = body["tasks"][0]
    assert "Michael" not in row["title"] and "Michael" not in (row["client"] or "")
    assert "michael.r@example.com" not in row["quote"]
    assert body["counts"]["unowned"] == 1 and body["unowned_open"] == 1
    assert {p["id"] for p in body["people"]} == {"irina", "rohan", "victor"}


def test_a_masked_title_hides_the_name_as_a_word_not_inside_words():
    from zoneinfo import ZoneInfo

    from app.services.tasks_view import task_row

    def title(name: str, text: str, masked: bool = True) -> str:
        conv = {**CONVS["c1"], "visitor_name": name}
        return task_row(task("t1", title=text), conv=conv, lead=None, names=NAMES, wrote_at=None, now=NOW,
                        tz=ZoneInfo("America/New_York"), masked=masked)["title"]

    assert title("Al Perez", "Call Al about the Alps, Al’s choice") == "Call •••ez about the Alps, •••ez’s choice"
    assert title("J. Smith", "Call J. back") == "Call •••th back"
    # Unmasked readers see the title as written.
    assert title("Al Perez", "Call Al about the Alps", masked=False) == "Call Al about the Alps"


def test_an_agent_can_t_open_someone_else_s_task():
    with patched(get_follow_up=AsyncMock(return_value=task("t9", assignee_id="rohan", created_by="rohan"))):
        r = client("sales", "victor").get("/api/tasks/t9")
    assert r.status_code == 404


def test_the_task_shows_where_it_came_from():
    made = NOW - timedelta(minutes=60)
    around = [
        {"id": "m0", "role": "user", "content": "Can you send me a couple of options?", "created_at": (made - timedelta(minutes=2)).isoformat()},
        {"id": "m-t1", "role": "agent", "content": "I’ll send you three options within the hour.", "created_at": made.isoformat()},
        {"id": "m1", "role": "user", "content": "Great, thanks.", "created_at": (made + timedelta(minutes=1)).isoformat()},
        {"id": "m2", "role": "user", "content": "Hi, any news?", "created_at": (NOW - timedelta(minutes=5)).isoformat()},
    ]
    events = [{"kind": "caught", "actor_id": "victor", "details": {"due_words": "within the hour", "due_kind": "said"},
               "at": made.isoformat()}]
    with patched(messages_around=AsyncMock(return_value=around), get_task_events=AsyncMock(return_value=events),
                 get_follow_up=AsyncMock(return_value=task("t1", created_at=made.isoformat()))):
        d = client("sales", "victor").get("/api/tasks/t1").json()["data"]
    assert [m["source"] for m in d["excerpt"]] == [False, True, False, False]
    # "Great, thanks." a minute after the promise isn't the client coming back; "any news?" 55 minutes later is.
    assert [m["after"] for m in d["excerpt"]] == [False, False, False, True]
    assert d["crm"]["in_crm"] is True and d["contact"]["email"] == "michael.r@example.com"
    texts = [h["text"] for h in d["history"]]
    assert texts[0] == "Caught in Victor Rusu’s message. Due within the hour, as written."
    assert texts.count("Michael wrote again in the chat.") == 1 and d["client_wrote_at"] == around[3]["created_at"]


def test_wrote_again_on_the_list_needs_a_pause_after_the_promise():
    made = NOW - timedelta(minutes=60)
    msgs = [{"conversation_id": "c1", "created_at": (made + timedelta(minutes=1)).isoformat()}]
    with patched(list_follow_ups=AsyncMock(return_value=([task("t1", created_at=made.isoformat())], [])),
                 client_messages_since=AsyncMock(return_value=msgs)):
        quick = client("sales", "victor").get("/api/tasks").json()["data"]["tasks"][0]
    msgs.append({"conversation_id": "c1", "created_at": (made + timedelta(minutes=40)).isoformat()})
    with patched(list_follow_ups=AsyncMock(return_value=([task("t1", created_at=made.isoformat())], [])),
                 client_messages_since=AsyncMock(return_value=msgs)):
        back = client("sales", "victor").get("/api/tasks").json()["data"]["tasks"][0]
    assert quick["client_wrote_at"] is None and back["client_wrote_at"] == msgs[1]["created_at"]


# ── What each role may change ────────────────────────────────────────────────

def test_an_agent_adds_tasks_only_for_themselves():
    with patched():
        own = client("sales", "victor").post("/api/tasks", json={"title": "Call the airline", "kind": "call"})
        other = client("sales", "victor").post("/api/tasks", json={"title": "x", "assignee_id": "rohan"})
        unowned = client("sales", "victor").post("/api/tasks", json={"title": "x", "unowned": True})
    assert own.status_code == 200 and own.json()["data"]["assignee_id"] == "victor"
    assert other.status_code == 403 and unowned.status_code == 403


def test_a_supervisor_gives_tasks_only_to_the_team():
    with patched():
        ok = client("supervisor", "irina").post("/api/tasks", json={"title": "Talk to Rohan", "assignee_id": "rohan", "source": "dashboard"})
        no = client("supervisor", "irina").post("/api/tasks", json={"title": "x", "assignee_id": "alex"})
    assert ok.status_code == 200 and ok.json()["data"]["source"] == "dashboard"
    assert no.status_code == 403


def test_done_snooze_and_not_a_promise():
    upd = AsyncMock(side_effect=lambda tid, p: {**task(tid), **p})
    ev = AsyncMock(return_value=None)
    with patched(update_follow_up=upd, add_task_event=ev):
        c = client("sales", "victor")
        assert c.patch("/api/tasks/t1", json={"done": True}).json()["data"]["done_by"] == "victor"
        later = (NOW + timedelta(hours=3)).isoformat()
        assert c.patch("/api/tasks/t1", json={"due_at": later}).status_code == 200
        assert c.patch("/api/tasks/t1", json={"not_a_promise": True}).json()["data"]["dismissed_by"] == "victor"
        assert c.patch("/api/tasks/t1", json={"assignee_id": "rohan"}).status_code == 403
    assert [call.args[1] for call in ev.call_args_list] == ["done", "due_changed", "dismissed"]


def test_only_a_caught_task_can_be_not_a_promise():
    with patched(get_follow_up=AsyncMock(return_value=task("t1", source="added"))):
        r = client("sales", "victor").patch("/api/tasks/t1", json={"not_a_promise": True})
    assert r.status_code == 400


def test_rules_are_for_owner_admin_dev():
    with patched(caught_stats_since=AsyncMock(return_value={"caught": 6, "not_a_promise": 1}),
                 save_task_rules=AsyncMock(return_value=None)):
        assert client("supervisor", "irina").get("/api/tasks/rules").status_code == 403
        got = client("owner", "alex").get("/api/tasks/rules").json()["data"]
        assert got["default_due"] == "2h" and got["last_7_days"] == {"caught": 6, "not_a_promise": 1}
        saved = client("owner", "alex").put("/api/tasks/rules", json={"default_due": "end_of_day", "leave_shift": "ready"}).json()["data"]
        assert saved["default_due"] == "end_of_day" and saved["leave_shift"] == "ready"
        assert client("owner", "alex").put("/api/tasks/rules", json={"phrases": []}).status_code == 400
        tried = client("owner", "alex").post("/api/tasks/rules/try", json={"sentence": "I'll e-mail you the fare rules by 5"}).json()["data"]
        assert tried["makes_task"] and tried["kind_label"] == "E-mail"


# ── Tasks that make themselves ───────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


def test_a_promise_in_an_agent_s_message_becomes_their_task():
    ins = AsyncMock(side_effect=lambda row: {"id": "new", **row})
    ev = AsyncMock(return_value=None)
    msg = {"id": "m1", "content": "Of course! I’ll send you three options within the hour.", "created_at": NOW.isoformat()}
    with patched(insert_follow_up=ins, add_task_event=ev):
        t = run(tasks_service.catch_from_agent_message(CONVS["c1"], msg, "victor"))
        none = run(tasks_service.catch_from_agent_message(CONVS["c1"], {"id": "m2", "content": "Great, thanks."}, "victor"))
    row = ins.call_args.kwargs.get("row") or ins.call_args.args[0]
    assert t["id"] == "new" and none is None and ins.call_count == 1
    assert row["title"] == "Send Michael three options for New York → London"
    assert row["assignee_id"] == "victor" and row["message_id"] == "m1" and row["due_kind"] == "said"
    assert row["due_at"] == (NOW + timedelta(hours=1)).isoformat()
    assert ev.call_args.args[1] == "caught"


def test_catching_never_fails_the_send():
    msg = {"id": "m1", "content": "I'll call you in an hour", "created_at": NOW.isoformat()}
    before = db.TASKS_HEALTH["catch_failures"]
    with patched(insert_follow_up=AsyncMock(side_effect=RuntimeError("db down"))):
        assert run(tasks_service.catch_from_agent_message(CONVS["c1"], msg, "victor")) is None
    assert db.TASKS_HEALTH["catch_failures"] == before + 1
    with patched(get_task_rules_raw=AsyncMock(side_effect=db.TasksNotSetUp("42703"))):
        assert run(tasks_service.catch_from_agent_message(CONVS["c1"], msg, "victor")) is None
    # Caught already (a retry): nothing new, no event.
    ev = AsyncMock(return_value=None)
    with patched(insert_follow_up=AsyncMock(return_value=None), add_task_event=ev):
        assert run(tasks_service.catch_from_agent_message(CONVS["c1"], msg, "victor")) is None
    assert ev.call_count == 0


def test_a_history_line_that_fails_never_undoes_the_task():
    # The task is saved before its history line: a line that can't be written
    # is counted, and the task still comes back (a "couldn't add" would have
    # the person add it twice; a Not-ready move would stop half way).
    ev = AsyncMock(side_effect=RuntimeError("task_events down"))
    msg = {"id": "m1", "content": "I'll call you in an hour", "created_at": NOW.isoformat()}
    before = dict(db.TASKS_HEALTH)
    upd = AsyncMock(side_effect=lambda tid, p: {**task(tid), **p})
    with patched(add_task_event=ev, update_follow_up=upd, open_task_ids_of=AsyncMock(return_value=["t1", "t2"])):
        caught = run(tasks_service.catch_from_agent_message(CONVS["c1"], msg, "victor"))
        asked = run(tasks_service.task_from_callback(CONVS["c1"], {"id": "u1", "created_at": NOW.isoformat()}, "Call me please"))
        made = client("sales", "victor").post("/api/tasks", json={"title": "Call the airline"})
        done = client("sales", "victor").patch("/api/tasks/t1", json={"done": True})
        moved = run(tasks_service.leave_shift("victor"))
    assert caught["id"] == "new" and asked["id"] == "new"
    assert made.status_code == 200 and made.json()["data"]["id"] == "new" and done.status_code == 200
    assert moved == 2 and [c.args[0] for c in upd.call_args_list][-2:] == ["t1", "t2"]
    assert db.TASKS_HEALTH["catch_failures"] == before["catch_failures"]
    assert db.TASKS_HEALTH["event_failures"] == before["event_failures"] + 6


def test_a_refused_task_row_is_logged_without_what_was_in_it(caplog):
    class Refused(Exception):
        code = "23514"

    boom = Refused('new row violates check constraint. Failing row contains (Send Michael three options, '
                   'Write to michael.r@example.com)')
    msg = {"id": "m1", "content": "I'll call you in an hour", "created_at": NOW.isoformat()}
    with patched(insert_follow_up=AsyncMock(side_effect=boom)), caplog.at_level("INFO"):
        assert run(tasks_service.catch_from_agent_message(CONVS["c1"], msg, "victor")) is None
        assert client("sales", "victor").post("/api/tasks", json={"title": "Send Michael the fares"}).status_code == 502
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 2
    for r in errors:
        text = r.getMessage()
        assert "Refused 23514 at " in text and "michael" not in text.lower() and r.exc_info is None


def test_a_call_me_while_the_ai_has_the_chat_is_unowned():
    ins = AsyncMock(side_effect=lambda row: {"id": "new", **row})
    conv = {**CONVS["c1"], "mode": "ai", "assigned_agent_id": None}
    with patched(insert_follow_up=ins):
        run(tasks_service.task_from_callback(conv, {"id": "u1", "created_at": NOW.isoformat()}, "Please call me, I'd rather talk."))
    row = ins.call_args.args[0]
    assert row["kind"] == "call" and row["source"] == "client_asked" and row["assignee_id"] is None
    assert row["unowned_reason"] == "ai_only" and row["title"] == "Call Michael back" and row["due_kind"] == "default"


@pytest.mark.parametrize("rule, ready, expected_to", [
    ("unowned", [{"id": "rohan"}], None),
    ("ready", [{"id": "rohan"}, {"id": "ana"}], "ana"),
    ("ready", [], None),
])
def test_going_not_ready_moves_open_tasks(rule, ready, expected_to):
    upd = AsyncMock(side_effect=lambda tid, p: {**task(tid), **p})
    with patched(get_task_rules_raw=AsyncMock(return_value={"leave_shift": rule}),
                 open_task_ids_of=AsyncMock(return_value=["t1", "t2"]),
                 ready_operators=AsyncMock(return_value=ready),
                 open_task_counts=AsyncMock(return_value={"rohan": 3, "ana": 1}),
                 update_follow_up=upd):
        moved = run(tasks_service.leave_shift("victor"))
    assert moved == 2
    payload = upd.call_args.args[1]
    assert payload["assignee_id"] == expected_to
    if expected_to is None:
        assert payload["unowned_reason"] == "left_shift" and payload["unowned_from"] == "victor"


# ── The page's numbers ───────────────────────────────────────────────────────

def test_late_today_later_and_on_time_this_week():
    rules = rules_of(None)
    NOW = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)   # a Wednesday afternoon in New York
    rows = [
        task("late", due_at=(NOW - timedelta(minutes=13)).isoformat()),
        task("soon", due_at=(NOW + timedelta(minutes=1)).isoformat()),
        task("next", due_at=(NOW + timedelta(days=8)).isoformat()),
    ]
    done = [task("ok", due_at=(NOW - timedelta(minutes=30)).isoformat(), done_at=(NOW - timedelta(minutes=40)).isoformat())]
    v = build_tasks_view(open_rows=rows, done_rows=done, convs=CONVS, leads=LEADS, client_msgs=[], names=NAMES,
                         now=NOW, rules=rules, masked=False)
    states = {r["id"]: r["state"] for r in v["tasks"]}
    assert states["late"] == "late" and states["next"] == "later"
    assert v["counts"]["late"] == 1 and v["on_time"]["on_time"] == 1
    assert v["on_time"] == {"on_time": 1, "due": 2}   # the late one counts against "on time"


# ── One chat's tasks (the chat's caught line and late banner) ────────────────

CHAT = "6f1d2c3b-4a5e-4f60-8a7b-9c0d1e2f3a4b"


def test_one_chat_s_tasks_are_filtered_in_the_query_and_skip_the_page_extras():
    lf = AsyncMock(return_value=([task("t1", conversation_id=CHAT)], []))
    people = AsyncMock(return_value=PEOPLE)
    with patched(list_follow_ups=lf, get_task_people=people):
        body = client("supervisor", "irina").get(f"/api/tasks?conversation_id={CHAT}").json()["data"]
        page = client("supervisor", "irina").get("/api/tasks").json()["data"]
    first, second = lf.call_args_list
    # In the query, not after it: the 500-row cap can't hide this chat's task.
    assert first.kwargs["conversation_id"] == CHAT and second.kwargs["conversation_id"] is None
    # Still the reader's scope: the supervisor's team and unowned.
    assert first.kwargs["assignee_ids"] == ["irina", "rohan", "victor"] and first.kwargs["with_unowned"] is True
    assert body["tasks"][0]["message_id"] == "m-t1" and body["people"] == [] and body["unowned_open"] is None
    assert people.await_count == 1 and page["unowned_open"] == 0


def test_an_agent_s_chat_view_is_still_only_their_own_and_a_bad_id_is_a_400():
    lf = AsyncMock(return_value=([], []))
    with patched(list_follow_ups=lf):
        ok = client("sales", "victor").get(f"/api/tasks?conversation_id={CHAT}")
        bad = client("sales", "victor").get("/api/tasks?conversation_id=not-a-chat")
    assert ok.status_code == 200 and lf.call_args.kwargs["assignee_ids"] == ["victor"]
    assert bad.status_code == 400 and lf.await_count == 1


LEAD = "0b8a7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d"
OTHER_CHAT = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6e"


def chat_row(**over) -> dict:
    """What get_blind_inputs reads: Victor's own sales chat, team 1."""
    base = {"id": CHAT, "status": "active", "mode": "human", "assigned_agent_id": "victor",
            "engaged_agent_id": None, "queued_at": None, "tunnel": "sales", "team_id": "team-1"}
    base.update(over)
    return base


def test_a_task_links_only_a_chat_its_maker_may_read():
    # The task's panel shows the chat's contact and the words around it: a
    # chat the maker can't open must not reach them through a task.
    def post(role, uid, row, cid=CHAT):
        ins = AsyncMock(side_effect=lambda r: {"id": "new", **r})
        with patched(insert_follow_up=ins, get_blind_inputs=AsyncMock(return_value=row)):
            r = client(role, uid).post("/api/tasks", json={"title": "Call back", "conversation_id": cid})
        return r, ins

    ok, ins = post("sales", "victor", chat_row())
    assert ok.status_code == 200 and ins.call_args.args[0]["conversation_id"] == CHAT
    tunnel, ins = post("sales", "victor", chat_row(tunnel="support"))
    assert tunnel.status_code == 403 and ins.await_count == 0
    # Waiting for a person and not his: the blind take holds here too.
    blind, ins = post("sales", "victor", chat_row(status="needs_agent", assigned_agent_id=None))
    assert blind.status_code == 403 and "Take the chat" in blind.json()["detail"] and ins.await_count == 0
    gone, ins = post("sales", "victor", None)
    assert gone.status_code == 404 and ins.await_count == 0
    team, _ = post("supervisor", "irina", chat_row(assigned_agent_id="rohan"))
    other_team, ins = post("supervisor", "irina", chat_row(team_id="team-9"))
    assert team.status_code == 200 and other_team.status_code == 403 and ins.await_count == 0
    # Managers read every chat; the chat must still exist.
    owner, _ = post("owner", "alex", chat_row(tunnel="support", team_id=None, status="needs_agent", assigned_agent_id=None))
    assert owner.status_code == 200


def test_a_chat_that_can_t_be_checked_or_isn_t_an_id_links_nothing():
    ins = AsyncMock(side_effect=lambda r: {"id": "new", **r})
    look = AsyncMock(side_effect=RuntimeError("network"))
    with patched(insert_follow_up=ins, get_blind_inputs=look):
        down = client("sales", "victor").post("/api/tasks", json={"title": "x", "conversation_id": CHAT})
        bad = client("sales", "victor").post("/api/tasks", json={"title": "x", "conversation_id": "chat-4810"})
        bad_lead = client("sales", "victor").post("/api/tasks", json={"title": "x", "lead_id": "l1"})
    assert down.status_code == 503 and bad.status_code == 400 and bad_lead.status_code == 400
    assert look.await_count == 1 and ins.await_count == 0


def test_a_task_made_on_a_lead_checks_the_lead_s_chat():
    def post(lead, row=None, **body):
        ins = AsyncMock(side_effect=lambda r: {"id": "new", **r})
        with patched(insert_follow_up=ins, get_lead_link=AsyncMock(return_value=lead),
                     get_blind_inputs=AsyncMock(return_value=row)):
            r = client("sales", "victor").post("/api/tasks", json={"title": "x", "lead_id": LEAD, **body})
        return r, ins

    ok, ins = post({"id": LEAD, "conversation_id": CHAT}, chat_row())
    assert ok.status_code == 200 and ins.call_args.args[0]["conversation_id"] == CHAT
    assert ins.call_args.args[0]["lead_id"] == LEAD
    support, ins = post({"id": LEAD, "conversation_id": CHAT}, chat_row(tunnel="support"))
    assert support.status_code == 403 and ins.await_count == 0
    missing, ins = post(None)
    assert missing.status_code == 404 and ins.await_count == 0
    # A lead and a chat sent together are one client's, or nothing is made.
    mixed, ins = post({"id": LEAD, "conversation_id": OTHER_CHAT}, chat_row(), conversation_id=CHAT)
    assert mixed.status_code == 400 and ins.await_count == 0
    # A lead with no chat links only the lead: there's no chat to show.
    alone, ins = post({"id": LEAD, "conversation_id": None})
    assert alone.status_code == 200 and ins.call_args.args[0]["conversation_id"] is None


def test_a_task_from_the_dashboard_keeps_the_line_it_came_from():
    ins = AsyncMock(side_effect=lambda row: {"id": "new", **row})
    with patched(insert_follow_up=ins):
        r = client("supervisor", "irina").post("/api/tasks", json={
            "title": "Talk to Rohan about chats going back to the AI", "assignee_id": "rohan",
            "source": "dashboard", "note": "  6 of his 11 chats went back to the AI today  "})
        plain = client("sales", "victor").post("/api/tasks", json={"title": "Call the airline", "note": "   "})
        long = client("sales", "victor").post("/api/tasks", json={"title": "x", "note": "y" * 301})
    assert r.status_code == 200 and ins.call_args_list[0].args[0]["quote"] == "6 of his 11 chats went back to the AI today"
    assert plain.status_code == 200 and ins.call_args_list[1].args[0]["quote"] is None
    assert long.status_code == 422 and ins.await_count == 2


class _Query:
    """A stand-in for the supabase query builder: records each step."""

    def __init__(self, log: list):
        self.log = log

    def __getattr__(self, name):
        def step(*args, **_kw):
            self.log.append((name, args))
            return self
        return step

    def execute(self):
        return type("Res", (), {"data": [{"id": "victor", "role": "sales"}]})()


def test_the_lead_read_tells_no_lead_from_a_failed_read():
    class _Rows(_Query):
        def __init__(self, log, rows=None, boom=None):
            super().__init__(log)
            self.rows, self.boom = rows, boom

        def execute(self):
            if self.boom:
                raise self.boom
            return type("Res", (), {"data": self.rows})()

    with patch.object(db, "get_client", return_value=_Rows([], rows=[{"id": "l1", "conversation_id": "c1"}])):
        assert run(db.get_lead_link("l1")) == {"id": "l1", "conversation_id": "c1"}
    with patch.object(db, "get_client", return_value=_Rows([], rows=[])):
        assert run(db.get_lead_link("l1")) is None
    with patch.object(db, "get_client", return_value=_Rows([], boom=RuntimeError("network"))):
        with pytest.raises(RuntimeError):
            run(db.get_lead_link("l1"))


class _Paged:
    """A query builder that serves `rows` one .range() page at a time, as PostgREST does (1000 at most)."""

    def __init__(self, rows: list, boom: Exception | None = None):
        self.rows, self.boom, self.ranges = rows, boom, []

    def __getattr__(self, name):
        return lambda *a, **k: self

    def range(self, lo, hi):
        self.ranges.append((lo, hi))
        return self

    def execute(self):
        if self.boom:
            raise self.boom
        lo, hi = self.ranges[-1]
        return type("Res", (), {"data": self.rows[lo:min(hi, lo + 999) + 1]})()


def test_the_task_counts_read_past_postgrest_s_1000_rows():
    # A count cut at 1000 would read Rohan short and give him the next tasks.
    q = _Paged([{"assignee_id": "rohan"}] * 1500 + [{"assignee_id": "ana"}] * 3)
    with patch.object(db, "get_client", return_value=q):
        assert run(db.open_task_counts(["rohan", "ana", "victor"])) == {"rohan": 1500, "ana": 3, "victor": 0}
    week = _Paged([{"id": str(i), "dismissed_at": NOW.isoformat() if i < 200 else None} for i in range(1200)])
    with patch.object(db, "get_client", return_value=week):
        assert run(db.caught_stats_since(NOW.isoformat())) == {"caught": 1200, "not_a_promise": 200}
    assert len(week.ranges) == 2


def test_the_task_counts_before_migration_051_say_not_set_up():
    missing = _Paged([], boom=Exception("column tasks.dismissed_at does not exist"))
    with patch.object(db, "get_client", return_value=missing):
        with pytest.raises(db.TasksNotSetUp):
            run(db.caught_stats_since(NOW.isoformat()))
        with pytest.raises(db.TasksNotSetUp):
            run(db.open_task_counts(["rohan"]))


def test_qa_isn_t_someone_a_task_can_go_to():
    log: list = []
    with patch.object(db, "get_client", return_value=_Query(log)):
        people = run(db.get_task_people())
    assert people == [{"id": "victor", "role": "sales"}]
    assert ("eq", ("is_active", True)) in log and ("neq", ("role", "qa")) in log


def test_a_time_written_into_the_history_names_its_zone():
    # The panel shows each person their own clock; a sentence written on the server can't, so it says whose clock.
    from zoneinfo import ZoneInfo

    from app.services.tasks_view import history_of

    ev = [{"kind": "due_changed", "actor_id": "victor", "details": {"due_at": "2026-11-09T15:00:00+00:00"}, "at": NOW.isoformat()}]
    assert history_of(ev, NAMES, client="Michael", tz=ZoneInfo("America/New_York"))[0]["text"] == \
        "Victor Rusu moved it to Mon 9 Nov, 10:00 New York time."
