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
        # And once more with only the number, typed the local way.
        lead(C3, 1, visitor_email=None, visitor_phone=None, phone_to_check="0412 345 678",
             route_display="NYC → ROM", destination_code="ROM"),
    ]
    out = build_offers(rows, now=NOW)
    assert len(out["waiting"]) == 1
    row = out["waiting"][0]
    assert row["conversation_id"] == C3, "the newest chat leads the row"
    assert row["route"] == "NYC → ROM", "the newest trip"
    assert row["email"] == "dana@example.com" and row["phone"] == "+61412345678"
    assert row["phone_to_check"] is None, "a placed number wins over one to check"
    assert {c["conversation_id"] for c in row["chats"]} == {C1, C2, C3}
    assert row["waiting_since"] == (NOW - timedelta(hours=20)).isoformat()


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


def universe(rows):
    return patch.multiple(
        db,
        get_leads_universe=AsyncMock(return_value=rows),
        get_user_names=AsyncMock(return_value=NAMES),
        get_team_ids_for_supervisor=AsyncMock(return_value=["team-1"]),
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
        body = client("owner").get("/api/offers").json()["data"]
        assert body["counts"]["waiting"] == 1 and body["can"] == {"mark": True} and body["days"] == 3
        qa = client("qa").get("/api/offers").json()["data"]
        assert qa["counts"]["waiting"] == 1 and qa["can"] == {"mark": False}


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


def test_the_list_reads_the_number_to_check_only_for_that():
    assert "phone_to_check:metadata->>phone_to_check" in db._LIST_COLUMNS_BASE


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
