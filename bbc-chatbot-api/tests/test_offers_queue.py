"""Offers to send (ADR-6b, ADR-8b) and "the client left, without a contact" (ADR-6c).

The queue holds every lead with a contact and a trip until someone marks the written offer sent or not
needed — one row per client, oldest first, with the channel they asked for. Who sees it follows the
Leads page and the chats: a supervisor their team's, masked; an agent their tunnel's, a chat still
waiting for a person blind until they take it; support and project managers none. A mark goes on the
conversation's metadata, merged server-side, and never moves a chat in the lists.

Every name, contact and route here is invented.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import offers  # noqa: E402
from app.db import supabase as db  # noqa: E402
from app.security.auth import get_current_user  # noqa: E402
from app.security.pii import mask_visitor_row  # noqa: E402
from app.services.offers_view import build_offers, channels, group_by_client, mark_patch, refusals  # noqa: E402

NOW = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
C1 = "aaaaaaaa-0000-4000-8000-000000000001"
C2 = "aaaaaaaa-0000-4000-8000-000000000002"
C3 = "aaaaaaaa-0000-4000-8000-000000000003"
AGENT = "11111111-1111-4111-8111-111111111111"
NAMES = {AGENT: "Agent One"}


def lead(cid: str, hours_ago: float, **over) -> dict:
    row = {
        "id": f"lead-{cid[-1]}", "conversation_id": cid, "chat_number": 100 + int(cid[-1]),
        "created_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "origin_code": "NYC", "destination_code": "LON", "route_display": "NYC → LON",
        "departure_date": "2026-12-10", "return_date": "2026-12-17", "passengers": 2, "cabin_class": "business",
        "visitor_name": "Dana Example", "visitor_email": "dana@example.com", "visitor_phone": None,
        "tunnel": "sales", "conversation_status": "closed", "conversation_mode": "ai", "team_id": "team-1",
        "created_in_crm": False, "crm_push_gate_reason": None,
    }
    row.update(over)
    return row


# ── The rows ─────────────────────────────────────────────────────────────────


def test_a_lead_with_a_contact_and_a_trip_waits_oldest_first():
    rows = [
        lead(C1, 1, visitor_email="one@example.com"),
        lead(C2, 30, visitor_email="two@example.com"),
        lead(C3, 5, visitor_email=None, visitor_phone="+12105550100"),
    ]
    out = build_offers(rows, now=NOW)
    assert [r["conversation_id"] for r in out["waiting"]] == [C2, C3, C1]
    assert out["counts"] == {"waiting": 3, "sent": 0, "not_needed": 0}
    first = out["waiting"][0]
    assert first["route"] == "NYC → LON" and first["email"] == "two@example.com"
    assert first["waiting_since"] == (NOW - timedelta(hours=30)).isoformat()


@pytest.mark.parametrize("over, why", [
    ({"visitor_email": None}, "no contact"),
    ({"origin_code": None, "destination_code": None}, "no trip: nothing to quote"),
    ({"tunnel": "support"}, "support's chats are not leads"),
    ({"crm_push_gate_reason": "test_email"}, "the CRM's check took it for a test"),
    ({"created_at": (NOW - timedelta(days=4)).isoformat()}, "older than the window"),
    ({"created_at": "not a time"}, "an unreadable time"),
])
def test_what_never_waits(over, why):
    assert build_offers([lead(C1, 1, **over)], now=NOW)["waiting"] == [], why


def test_the_window_is_one_three_seven_or_fourteen_days():
    old = lead(C1, 24 * 5)
    assert build_offers([old], now=NOW, days=3)["waiting"] == []
    assert len(build_offers([old], now=NOW, days=7)["waiting"]) == 1
    # Anything else reads as the default three days.
    assert build_offers([old], now=NOW, days=5)["waiting"] == []


def test_an_email_only_lead_waits_like_any_other():
    """ADR-8b: one contact is enough here, whatever the CRM takes."""
    row = build_offers([lead(C1, 2, visitor_phone=None)], now=NOW)["waiting"][0]
    assert row["email"] == "dana@example.com" and row["phone"] is None
    assert row["crm_state"] == "no_phone"


def test_a_number_to_check_is_a_contact_and_shown_as_such():
    """ADR-6f: a local number with no country to place it waits for the agent to check, never dialled."""
    row = build_offers([lead(C1, 2, visitor_email=None, phone_to_check="0412 345 678")], now=NOW)["waiting"][0]
    assert row["phone"] is None and row["phone_to_check"] == "0412 345 678"


def test_one_client_one_row_newest_trip_oldest_wait():
    rows = [
        lead(C1, 20, visitor_email="dana@example.com", visitor_phone=None, route_display="NYC → LON"),
        # Came back with a phone and the same e-mail, for another trip.
        lead(C2, 2, visitor_email="dana@example.com", visitor_phone="+61412345678", route_display="NYC → PAR",
             destination_code="PAR"),
        # And once more with that phone only.
        lead(C3, 1, visitor_email=None, visitor_phone="+61 412 345 678", route_display="NYC → ROM",
             destination_code="ROM"),
    ]
    out = build_offers(rows, now=NOW)
    assert len(out["waiting"]) == 1
    row = out["waiting"][0]
    assert row["conversation_id"] == C3, "the newest chat leads the row"
    assert row["route"] == "NYC → ROM", "the newest trip"
    assert row["email"] == "dana@example.com" and row["phone"] in ("+61412345678", "+61 412 345 678")
    assert {c["conversation_id"] for c in row["chats"]} == {C1, C2, C3}
    assert row["waiting_since"] == (NOW - timedelta(hours=20)).isoformat()


def test_numbers_that_only_end_alike_are_two_clients():
    """+1 210 555 0100 and +1 310 555 0100 end in the same nine digits and are two people (CodeRabbit #270)."""
    rows = [lead(C1, 2, visitor_email="one@example.com", visitor_phone="+12105550100"),
            lead(C2, 1, visitor_email="two@example.com", visitor_phone="+13105550100")]
    assert len(build_offers(rows, now=NOW)["waiting"]) == 2


def test_a_number_to_check_matches_only_the_same_number_to_check():
    """A local number has no country: nothing says it is the placed +61 one, so it is not merged with it.
    The same local number in two chats is one client."""
    rows = [
        lead(C1, 3, visitor_email=None, visitor_phone="+61412345678"),
        lead(C2, 2, visitor_email=None, visitor_phone=None, phone_to_check="0412 345 678"),
        lead(C3, 1, visitor_email=None, visitor_phone=None, phone_to_check="0412-345-678"),
    ]
    groups = group_by_client(rows)
    assert sorted(len(g) for g in groups) == [1, 2]
    assert {r["conversation_id"] for g in groups if len(g) == 2 for r in g} == {C2, C3}


def test_two_clients_stay_two_rows():
    groups = group_by_client([lead(C1, 1, visitor_email="a@example.com"), lead(C2, 1, visitor_email="b@example.com")])
    assert len(groups) == 2


@pytest.mark.parametrize("signals, wished, turned_down", [
    ({"contact_channel": "whatsapp"}, ["whatsapp"], []),
    ({"contact_channel": "email, whatsapp"}, ["whatsapp", "email"], []),
    ({"contact_channel": "text, no calls"}, ["text"], ["no calls"]),
    ({"callback_requested": "true"}, ["call"], []),
    ({"callback_requested": "false", "contact_channel": "no email"}, [], ["no email"]),
    ({}, [], []),
])
def test_the_channel_the_client_asked_for(signals, wished, turned_down):
    assert channels(signals) == wished
    assert refusals(signals) == turned_down
    row = build_offers([lead(C1, 1, **signals)], now=NOW)["waiting"][0]
    assert row["channels"] == wished and row["refused"] == turned_down


def test_the_price_the_client_saw_and_the_crm_state_ride_along():
    row = build_offers([lead(C1, 1, visitor_phone="+12105550100", price_seen="$2,380",
                             crm_push_gate_reason="crm_422:client.email", assigned_agent_id=AGENT)],
                       now=NOW, names=NAMES)["waiting"][0]
    assert row["price_seen"] == "$2,380"
    assert row["crm_state"] == "refused" and row["crm_gate"] == "crm_422:client.email"
    assert row["agent_name"] == "Agent One"


def test_marked_leads_leave_the_queue_and_show_who_and_when():
    sent_at = (NOW - timedelta(minutes=30)).isoformat()
    rows = [
        lead(C1, 3, visitor_email="one@example.com", offer_sent_at=sent_at, offer_sent_by=AGENT),
        lead(C2, 3, visitor_email="two@example.com", offer_skipped_at=sent_at, offer_skipped_by=AGENT),
        lead(C3, 3, visitor_email="three@example.com"),
    ]
    out = build_offers(rows, now=NOW, names=NAMES)
    assert [r["conversation_id"] for r in out["waiting"]] == [C3]
    states = {r["conversation_id"]: (r["state"], r["by_name"]) for r in out["done"]}
    assert states == {C1: ("sent", "Agent One"), C2: ("not_needed", "Agent One")}
    assert out["counts"] == {"waiting": 1, "sent": 1, "not_needed": 1}


def test_masked_for_a_supervisor():
    row = build_offers([lead(C1, 1, visitor_phone="+12105550100", phone_to_check=None)], now=NOW,
                       masked=True)["waiting"][0]
    assert row["email"] == "da•••@example.com"
    assert row["phone"] == "•••0100"
    assert row["client"] != "Dana Example"
    to_check = build_offers([lead(C1, 1, visitor_email=None, phone_to_check="0412 345 678")], now=NOW,
                            masked=True)["waiting"][0]
    assert to_check["phone_to_check"] == "•••5678"


def test_blind_rows_say_who_and_since_when_never_the_trip_or_the_contact():
    row = build_offers([lead(C1, 1, visitor_phone="+12105550100", price_seen="$900")], now=NOW,
                       blind=lambda r: True)["waiting"][0]
    assert row["blind"] is True and row["client"] == "Dana Example"
    for key in ("email", "phone", "phone_to_check", "route", "departure_date", "passengers", "price_seen",
                "crm_state", "crm_gate", "crm_lead_id"):
        assert row[key] is None, key
    assert row["channels"] == [] and row["refused"] == []
    assert row["waiting_since"]


def test_a_blind_row_keeps_the_wish_back_too():
    row = build_offers([lead(C1, 1, contact_channel="whatsapp, no calls")], now=NOW, blind=lambda r: True)["waiting"][0]
    assert row["channels"] == [] and row["refused"] == []


def test_mark_patch_sets_one_and_clears_the_other():
    sent = mark_patch("sent", AGENT, NOW)
    assert sent["offer_sent_at"] == NOW.isoformat() and sent["offer_sent_by"] == AGENT
    assert sent["offer_skipped_at"] is None and sent["offer_skipped_by"] is None
    skipped = mark_patch("not_needed", AGENT, NOW)
    assert skipped["offer_skipped_at"] and skipped["offer_sent_at"] is None
    assert set(mark_patch("open", AGENT, NOW).values()) == {None}


# ── The endpoints ────────────────────────────────────────────────────────────


def client(role: str, uid: str = AGENT, **extra) -> TestClient:
    app = FastAPI()
    app.include_router(offers.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": uid, "role": role, **extra}
    return TestClient(app)


def _state(row):
    """A universe row's chat as get_blind_inputs_many reads it now."""
    return {"id": row["conversation_id"], "status": row.get("conversation_status"), "mode": row.get("conversation_mode"),
            "assigned_agent_id": row.get("assigned_agent_id"), "queued_at": row.get("queued_at"),
            "engaged_agent_id": row.get("engaged_agent_id")}


def universe(rows, now=None):
    """The cached universe, and the chats' state now (`now`: {id: fields that changed since}).
    get_blind_inputs_many is a MagicMock-backed AsyncMock: its calls are kept for the asserts."""
    states = {r["conversation_id"]: {**_state(r), **((now or {}).get(r["conversation_id"]) or {})} for r in rows}
    return patch.multiple(
        db,
        get_leads_universe=AsyncMock(return_value=rows),
        get_user_names=AsyncMock(return_value=NAMES),
        get_team_ids_for_supervisor=AsyncMock(return_value=["team-1"]),
        get_blind_inputs_many=AsyncMock(side_effect=lambda ids: {i: states[i] for i in ids if i in states}),
    )


def recent(cid, **over):
    """A lead made a moment ago, in the window whatever the clock says."""
    return lead(cid, 0, created_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), **over)


@pytest.mark.parametrize("role", ["support", "project_manager", "viewer"])
def test_who_gets_no_queue(role):
    with universe([recent(C1)]):
        assert client(role).get("/api/offers").status_code == 403


def test_the_days_must_be_a_window():
    with universe([recent(C1)]):
        assert client("owner").get("/api/offers?days=5").status_code == 400


def test_a_failed_read_is_a_502_never_an_empty_queue():
    with patch.object(db, "get_leads_universe", AsyncMock(side_effect=RuntimeError("down"))):
        assert client("owner").get("/api/offers").status_code == 502


def test_owner_sees_all_and_may_mark_qa_reads_only():
    with universe([recent(C1)]):
        resp = client("owner").get("/api/offers").json()
        body = resp["data"]
        assert resp["success"] is True and resp["count"] == 1, "the admin envelope: success, data, count"
        assert body["counts"]["waiting"] == 1 and body["can"] == {"mark": True} and body["days"] == 3
        qa = client("qa").get("/api/offers").json()["data"]
        assert qa["counts"]["waiting"] == 1 and qa["can"] == {"mark": False}
        assert not db.get_blind_inputs_many.called, "a manager is never blind: nothing to read"


def test_supervisor_sees_their_team_masked():
    rows = [recent(C1), recent(C2, visitor_email="other@example.com", team_id="team-2")]
    with universe(rows):
        body = client("supervisor").get("/api/offers").json()["data"]
    assert [r["conversation_id"] for r in body["waiting"]] == [C1]
    assert body["waiting"][0]["email"] == "da•••@example.com"


def test_supervisor_without_a_team_sees_nothing():
    with universe([recent(C1)]), patch.object(db, "get_team_ids_for_supervisor", AsyncMock(return_value=[])):
        assert client("supervisor").get("/api/offers").json()["data"]["waiting"] == []


def test_an_agent_sees_their_tunnel_and_a_waiting_chat_blind():
    rows = [
        recent(C1, visitor_email="closed@example.com"),
        recent(C2, visitor_email="waiting@example.com", conversation_status="needs_agent"),
        recent(C3, visitor_email="theirs@example.com", conversation_status="active", assigned_agent_id=AGENT),
    ]
    with universe(rows):
        body = client("sales", tunnel_scope="sales").get("/api/offers").json()["data"]
    by_id = {r["conversation_id"]: r for r in body["waiting"]}
    assert by_id[C1]["blind"] is False and by_id[C1]["email"] == "closed@example.com"
    assert by_id[C2]["blind"] is True and by_id[C2]["email"] is None and by_id[C2]["route"] is None
    assert by_id[C3]["blind"] is False, "the agent's own chat is never blind"
    assert body["can"] == {"mark": True}


def test_a_chat_that_entered_the_line_since_the_cache_is_blind_already():
    """The universe is cached for 20 s; the blind rule reads the chats' state now (CodeRabbit #270)."""
    rows = [recent(C1, visitor_email="closed@example.com")]
    with universe(rows, now={C1: {"status": "needs_agent", "queued_at": "2026-11-18T14:59:00+00:00"}}):
        body = client("sales", tunnel_scope="sales").get("/api/offers").json()["data"]
        asked = db.get_blind_inputs_many.call_args.args[0]
    row = body["waiting"][0]
    assert row["blind"] is True and row["email"] is None and row["route"] is None
    assert asked == [C1], "only the queue's chats are read"


def test_a_failed_blind_read_hides_every_row():
    with universe([recent(C1)]), patch.object(db, "get_blind_inputs_many", AsyncMock(side_effect=RuntimeError("down"))):
        body = client("sales", tunnel_scope="sales").get("/api/offers").json()["data"]
    assert body["waiting"][0]["blind"] is True and body["waiting"][0]["email"] is None
    assert offers.OFFERS_HEALTH["blind_read_failed"] >= 1


def _conv(cid, **over):
    return {"id": cid, "tunnel": "sales", "team_id": "team-1", "status": "closed", "mode": "ai", **over}


def test_mark_saves_each_chat_of_the_client_and_refreshes_the_queue():
    patched = AsyncMock(return_value=True)
    with patch.multiple(
        db,
        get_conversation_simple=AsyncMock(side_effect=lambda cid: _conv(cid)),
        get_blind_inputs=AsyncMock(side_effect=lambda cid: _conv(cid)),
        patch_conversation_presence=patched,
        invalidate_leads_universe=MagicMock(),
    ):
        resp = client("sales").post(f"/api/offers/{C1}", json={"state": "sent", "chats": [C2, C1]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"] == 2
        assert db.invalidate_leads_universe.called
    assert [c.args[0] for c in patched.call_args_list] == [C1, C2]
    payload = patched.call_args_list[0].args[1]
    assert payload["offer_sent_by"] == AGENT and payload["offer_sent_at"] and payload["offer_skipped_at"] is None


def test_undo_opens_it_again():
    patched = AsyncMock(return_value=True)
    with patch.multiple(db, get_conversation_simple=AsyncMock(return_value=_conv(C1)),
                        get_blind_inputs=AsyncMock(return_value=_conv(C1)), patch_conversation_presence=patched,
                        invalidate_leads_universe=MagicMock()):
        assert client("owner").post(f"/api/offers/{C1}", json={"state": "open"}).status_code == 200
    assert set(patched.call_args.args[1].values()) == {None}


@pytest.mark.parametrize("role, conv, blind, status", [
    ("qa", _conv(C1), _conv(C1), 403),
    ("project_manager", _conv(C1), _conv(C1), 403),
    ("supervisor", _conv(C1, team_id="team-2"), _conv(C1), 403),
    ("sales", _conv(C1, tunnel="support"), _conv(C1), 403),
    ("sales", _conv(C1, status="needs_agent"), _conv(C1, status="needs_agent"), 403),
    ("owner", None, None, 404),
])
def test_who_may_not_mark(role, conv, blind, status):
    patched = AsyncMock(return_value=True)
    with patch.multiple(db, get_conversation_simple=AsyncMock(return_value=conv),
                        get_blind_inputs=AsyncMock(return_value=blind), patch_conversation_presence=patched,
                        get_team_ids_for_supervisor=AsyncMock(return_value=["team-1"]),
                        invalidate_leads_universe=MagicMock()):
        resp = client(role, tunnel_scope="sales").post(f"/api/offers/{C1}", json={"state": "sent"})
    assert resp.status_code == status, resp.text
    assert not patched.called


def test_a_client_with_many_chats_is_marked_whole():
    """22 chats of one client: every one is marked, none left out (CodeRabbit #270)."""
    ids = [f"aaaaaaaa-0000-4000-8000-{i:012d}" for i in range(1, 23)]
    patched = AsyncMock(return_value=True)
    with patch.multiple(db, get_conversation_simple=AsyncMock(side_effect=lambda cid: _conv(cid)),
                        get_blind_inputs=AsyncMock(side_effect=lambda cid: _conv(cid)),
                        patch_conversation_presence=patched, invalidate_leads_universe=MagicMock()):
        resp = client("owner").post(f"/api/offers/{ids[0]}", json={"state": "sent", "chats": ids[1:]})
    assert resp.status_code == 200, resp.text
    assert [c.args[0] for c in patched.call_args_list] == ids


def test_a_mark_that_fails_halfway_puts_the_others_back():
    """One client, one mark: the second chat's save fails, so the first goes back to what it was, and the
    queue is read again (CodeRabbit #270)."""
    before = {"offer_sent_at": None, "offer_sent_by": None, "offer_skipped_at": "2026-11-17T09:00:00+00:00",
              "offer_skipped_by": AGENT}
    calls = []
    stored = {C1: dict(before), C2: dict(before)}

    async def save(cid, patch_):
        calls.append((cid, patch_))
        if cid == C2 and patch_.get("offer_sent_at"):
            return False
        stored[cid].update(patch_)
        return True

    with patch.multiple(db, get_conversation_simple=AsyncMock(side_effect=lambda cid: _conv(cid, metadata=dict(stored[cid]))),
                        get_blind_inputs=AsyncMock(side_effect=lambda cid: _conv(cid)),
                        patch_conversation_presence=AsyncMock(side_effect=save), invalidate_leads_universe=MagicMock()):
        resp = client("owner").post(f"/api/offers/{C1}", json={"state": "sent", "chats": [C2]})
        assert db.invalidate_leads_universe.called
    assert resp.status_code == 503
    assert [c[0] for c in calls] == [C1, C2, C1]
    assert calls[-1][1] == before, "the first chat is back as it was"
    assert stored[C1] == before


def test_an_undo_never_writes_over_a_mark_another_request_saved_since():
    """Request A marks the first chat, fails on the second; meanwhile B marked the first chat too. A's undo
    finds B's mark there, not its own, and leaves it (CodeRabbit #270, round 2)."""
    other = {"offer_sent_at": "2026-11-18T15:00:01+00:00", "offer_sent_by": "someone-else",
             "offer_skipped_at": None, "offer_skipped_by": None}
    calls = []

    async def save(cid, patch_):
        calls.append((cid, patch_))
        return cid != C2

    reads = {"n": 0}

    async def simple(cid):
        reads["n"] += 1
        # The checks before the mark read nothing marked; the undo's read finds B's mark.
        return _conv(cid, metadata=dict(other) if reads["n"] > 2 else {})

    with patch.multiple(db, get_conversation_simple=AsyncMock(side_effect=simple),
                        get_blind_inputs=AsyncMock(side_effect=lambda cid: _conv(cid)),
                        patch_conversation_presence=AsyncMock(side_effect=save), invalidate_leads_universe=MagicMock()):
        resp = client("owner").post(f"/api/offers/{C1}", json={"state": "sent", "chats": [C2]})
    assert resp.status_code == 503
    assert [c[0] for c in calls] == [C1, C2], "no undo written over B's mark"
    assert offers.OFFERS_HEALTH["rollback_skipped"] >= 1


def test_a_failed_save_that_landed_anyway_is_put_back_too():
    """The second chat's save timed out after the write landed: it goes back with the first, so the client
    never stands half marked (CodeRabbit #270, round 3)."""
    before = {"offer_sent_at": None, "offer_sent_by": None, "offer_skipped_at": "2026-11-17T09:00:00+00:00",
              "offer_skipped_by": AGENT}
    stored = {C1: dict(before), C2: dict(before)}

    async def save(cid, patch_):
        stored[cid].update(patch_)  # the write lands…
        return not (cid == C2 and patch_.get("offer_sent_at"))  # …and the answer is lost

    with patch.multiple(db, get_conversation_simple=AsyncMock(side_effect=lambda cid: _conv(cid, metadata=dict(stored[cid]))),
                        get_blind_inputs=AsyncMock(side_effect=lambda cid: _conv(cid)),
                        patch_conversation_presence=AsyncMock(side_effect=save), invalidate_leads_universe=MagicMock()):
        resp = client("owner").post(f"/api/offers/{C1}", json={"state": "sent", "chats": [C2]})
    assert resp.status_code == 503
    assert stored[C1] == before and stored[C2] == before


def test_the_queue_is_read_again_even_when_the_first_save_fails():
    """The first save can land before its answer is lost: the queue is read again either way."""
    with patch.multiple(db, get_conversation_simple=AsyncMock(return_value=_conv(C1)),
                        get_blind_inputs=AsyncMock(return_value=_conv(C1)),
                        patch_conversation_presence=AsyncMock(return_value=False),
                        invalidate_leads_universe=MagicMock()):
        assert client("owner").post(f"/api/offers/{C1}", json={"state": "sent"}).status_code == 503
        assert db.invalidate_leads_universe.called


def test_mark_rejects_a_bad_chat_id_and_a_failed_save_is_a_503():
    with patch.multiple(db, get_conversation_simple=AsyncMock(return_value=_conv(C1)),
                        get_blind_inputs=AsyncMock(return_value=_conv(C1)),
                        patch_conversation_presence=AsyncMock(return_value=False),
                        invalidate_leads_universe=MagicMock()):
        assert client("owner").post("/api/offers/not-a-chat", json={"state": "sent"}).status_code == 400
        assert client("owner").post(f"/api/offers/{C1}", json={"state": "maybe"}).status_code == 422
        assert client("owner").post(f"/api/offers/{C1}", json={"state": "sent"}).status_code == 503


# ── "The client left, without a contact" (ADR-6c) ────────────────────────────


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        res = MagicMock()
        res.data = self._rows
        res.count = len(self._rows)
        return res


@pytest.mark.asyncio
@pytest.mark.parametrize("fields, expected", [
    ({"visitor_email": "dana@example.com"}, True),
    ({"visitor_phone": "+12105550100"}, True),
    ({"phone_to_check": "0412 345 678"}, True),
    # The fallback projection: the contact under its own names, never in the row (CodeRabbit #270).
    ({"contact_email": "dana@example.com"}, True),
    ({"contact_phone": "+12105550100"}, True),
    ({"visitor_email": "  "}, False),
    ({}, False),
])
async def test_each_list_row_says_whether_a_contact_was_left(fields, expected):
    row = {"id": C1, "mode": "ai", "assigned_agent_id": None, "engaged_agent_id": None, **fields}
    fake = MagicMock()
    fake.table.return_value = _FakeQuery([row])
    with patch.object(db, "get_client", return_value=fake), \
            patch.object(db, "get_users_by_ids", AsyncMock(return_value={})), \
            patch.object(db, "_supervisor_columns_available", return_value=False):
        out, _ = await db.get_conversations()
    assert out[0]["has_contact"] is expected
    assert "phone_to_check" not in out[0] and "phone_to_check" not in out[0]["metadata"]
    assert "contact_email" not in out[0] and "contact_phone" not in out[0]


def test_the_list_reads_the_number_to_check_only_for_that():
    assert "phone_to_check:metadata->>phone_to_check" in db._LIST_COLUMNS_BASE
    assert "contact_email:visitor_email,contact_phone:visitor_phone" in db._LIST_COLUMNS_BASE


def _detail(role: str, conv: dict):
    from app.api import conversations as mod

    app = FastAPI()
    app.include_router(mod.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {"id": AGENT, "role": role, "tunnel_scope": "sales"}
    with patch.object(mod.db, "get_conversation", AsyncMock(return_value=conv)), \
            patch.object(mod.db, "get_team_ids_for_supervisor", AsyncMock(return_value=["team-1"])):
        return TestClient(app).get(f"/api/conversations/{C1}").json()["data"]


def test_the_detail_says_it_before_masking_or_blinding():
    conv = {"id": C1, "tunnel": "sales", "team_id": "team-1", "status": "needs_agent", "mode": "waiting_for_agent",
            "visitor_email": None, "visitor_phone": None, "messages": [],
            "metadata": {"phone_to_check": "0412 345 678", "widget_presence": "left"}}
    blind = _detail("sales", conv)
    assert blind["has_contact"] is True
    assert "phone_to_check" not in blind["metadata"], "a waiting chat keeps the number from an agent"
    masked = _detail("supervisor", conv)
    assert masked["has_contact"] is True and masked["metadata"]["phone_to_check"] == "•••5678"
    none = _detail("owner", {**conv, "metadata": {}})
    assert none["has_contact"] is False


def test_mask_visitor_row_masks_the_number_to_check():
    row = mask_visitor_row({"phone_to_check": "0412 345 678", "metadata": {"phone_to_check": "0412 345 678"}})
    assert row["phone_to_check"] == "•••5678" and row["metadata"]["phone_to_check"] == "•••5678"


# ── The universe without conversations.queued_at ────────────────────────────


@pytest.mark.asyncio
async def test_the_universe_reads_without_queued_at_when_the_column_is_missing(monkeypatch):
    """034 not applied: the universe is read without queued_at, as the chat list does, not a 502
    (CodeRabbit #270). Any other failure is not retried."""
    from postgrest.exceptions import APIError

    seen = []

    async def pages(build, **_k):
        query = build()
        cols = query.cols
        seen.append(cols)
        if "queued_at" in cols:
            raise APIError({"code": "42703", "message": "column conversations.queued_at does not exist"})
        return [{"id": C1, "status": "closed"}]

    class _Q:
        def __init__(self):
            self.cols = ""

        def select(self, cols):
            self.cols = cols
            return self

        def order(self, *_a):
            return self

    class _Client:
        def table(self, _name):
            return _Q()

    monkeypatch.setattr(db, "_select_pages", pages)
    monkeypatch.setattr(db, "_queued_at_ok", None)
    monkeypatch.setattr(db, "_queued_at_downgraded_at", None)
    rows = await db._universe_convs(_Client())
    assert rows == [{"id": C1, "status": "closed"}]
    assert "queued_at" in seen[0] and "queued_at" not in seen[1]
    assert "engaged_agent_id" in seen[1], "the other blind inputs stay"

    async def broken(build, **_k):
        raise RuntimeError("timeout")

    monkeypatch.setattr(db, "_select_pages", broken)
    monkeypatch.setattr(db, "_queued_at_ok", None)
    with pytest.raises(RuntimeError):
        await db._universe_convs(_Client())
