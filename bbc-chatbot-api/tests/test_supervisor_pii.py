"""Supervisor PII minimization — masked contact fields + stripped marketing meta.

Masking is supervisor-only and enforced server-side (raw PII never reaches the
client for that role). All other roles get full, unmasked data.
"""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

with patch("app.db.supabase.get_client"):
    from app.main import app

from app.security.auth import get_current_user
from app.security.pii import (
    mask_name, mask_phone, mask_email, strip_marketing_metadata,
)

SUP = "11111111-1111-1111-1111-111111111111"


def _as(role: str, user_id: str = "u-1"):
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id, "role": role, "email": f"{role}@bbc.com",
        "name": role, "tunnel_scope": "all",
    }


@pytest.fixture(autouse=True)
def _clear():
    yield
    app.dependency_overrides.clear()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _row(**over):
    base = {
        "id": "c-1", "tunnel": "sales", "status": "closed", "team_id": "alpha",
        "visitor_name": "Emily", "visitor_phone": "+14157179051",
        "visitor_email": "tristud2002@yahoo.com",
        "metadata": {"utm_source": "google", "gclid": "xyz", "site": "bbc", "widget_open": True},
    }
    base.update(over)
    return base


# ── 1. Unit: mask helpers ────────────────────────────────────────────


def test_mask_name():
    assert mask_name("Emily") == "\u2022\u2022\u2022ly"
    assert mask_name("Al") == "\u2022\u2022"
    assert mask_name("") == ""
    assert mask_name(None) is None


def test_mask_phone():
    assert mask_phone("+14157179051") == "\u2022\u2022\u20229051"
    assert mask_phone("12") == "\u2022\u2022\u2022"
    assert mask_phone(None) is None


def test_mask_email():
    assert mask_email("tristud2002@yahoo.com") == "tr\u2022\u2022\u2022@yahoo.com"
    assert mask_email("a@b.com") == "a\u2022\u2022\u2022@b.com"
    assert mask_email("notanemail") == "notanemail"
    assert mask_email(None) is None


# ── 2. Unit: marketing stripper ──────────────────────────────────────


def test_strip_marketing_metadata():
    meta = {"utm_source": "g", "utm_medium": "cpc", "gclid": "x", "fbclid": "y",
            "referrer": "r", "page_url": "p", "site": "bbc", "widget_open": True}
    out = strip_marketing_metadata(meta)
    assert "utm_source" not in out and "gclid" not in out and "referrer" not in out
    # non-marketing keys preserved
    assert out.get("site") == "bbc"
    assert out.get("widget_open") is True
    assert strip_marketing_metadata(None) == {}


# ── 3. Conversations: supervisor masked (list + detail) ──────────────


@pytest.mark.asyncio
async def test_supervisor_conversations_list_masked():
    with (
        patch("app.db.supabase.get_team_ids_for_supervisor", new_callable=AsyncMock, return_value=["alpha"]),
        patch("app.db.supabase.get_conversations", new_callable=AsyncMock, return_value=([_row()], 1)),
    ):
        _as("supervisor", SUP)
        async with _client() as c:
            r = await c.get("/api/conversations")
    row = r.json()["data"][0]
    assert row["visitor_name"] == "\u2022\u2022\u2022ly"
    assert row["visitor_phone"] == "\u2022\u2022\u20229051"
    assert row["visitor_email"] == "tr\u2022\u2022\u2022@yahoo.com"
    assert "utm_source" not in row["metadata"] and "gclid" not in row["metadata"]
    assert row["metadata"].get("site") == "bbc"


@pytest.mark.asyncio
async def test_supervisor_conversation_detail_masked():
    conv = _row(status="active", messages=[{"id": "m1"}])
    with (
        patch("app.db.supabase.get_team_ids_for_supervisor", new_callable=AsyncMock, return_value=["alpha"]),
        patch("app.db.supabase.get_conversation", new_callable=AsyncMock, return_value=conv),
    ):
        _as("supervisor", SUP)
        async with _client() as c:
            r = await c.get("/api/conversations/c-1")
    data = r.json()["data"]
    assert data["visitor_name"] == "\u2022\u2022\u2022ly"
    assert data["visitor_phone"] == "\u2022\u2022\u20229051"
    assert data["visitor_email"] == "tr\u2022\u2022\u2022@yahoo.com"
    assert "gclid" not in data["metadata"]


# ── 4/5. Leads: supervisor masked (list + detail) ────────────────────


@pytest.mark.asyncio
async def test_supervisor_leads_list_masked():
    lead_row = {"id": "l-1", "visitor_name": "Emily", "visitor_phone": "+14157179051",
                "visitor_email": "tristud2002@yahoo.com"}
    with (
        patch("app.db.supabase.get_team_ids_for_supervisor", new_callable=AsyncMock, return_value=["alpha"]),
        patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=([lead_row], 1)),
        patch("app.db.supabase.get_leads_review_counts", new_callable=AsyncMock, return_value=(0, 0)),
    ):
        _as("supervisor", SUP)
        async with _client() as c:
            r = await c.get("/api/leads")
    row = r.json()["data"][0]
    assert row["visitor_name"] == "\u2022\u2022\u2022ly"
    assert row["visitor_phone"] == "\u2022\u2022\u20229051"
    assert row["visitor_email"] == "tr\u2022\u2022\u2022@yahoo.com"


@pytest.mark.asyncio
async def test_supervisor_lead_detail_masked():
    lead = {
        "id": "l-1", "conversation_id": "c-1", "trip_type": "round_trip",
        "cabin_class": "business", "passengers": 2, "flexible_dates": True,
        "origin_code": "JFK", "destination_code": "LHR",
        "departure_date": "2027-03-15", "return_date": "2027-03-22",
        "route_display": "JFK → LHR", "score": 50, "status": "new", "tier": "silver",
        "intent_signals": [], "notes": "",
        "created_at": "2026-07-20T10:00:00Z", "updated_at": "2026-07-20T10:00:00Z",
        "contacted_at": None, "converted_at": None,
        "visitor_name": "Emily", "visitor_phone": "+14157179051",
        "visitor_email": "tristud2002@yahoo.com",
        "reviewed_by_qa": False, "route_segments": [], "status_history": [],
    }
    with patch("app.db.supabase.get_lead_full", new_callable=AsyncMock, return_value=lead):
        _as("supervisor", SUP)
        async with _client() as c:
            r = await c.get("/api/leads/l-1")
    data = r.json()
    assert data["visitor_name"] == "\u2022\u2022\u2022ly"
    assert data["visitor_phone"] == "\u2022\u2022\u20229051"
    assert data["visitor_email"] == "tr\u2022\u2022\u2022@yahoo.com"


# ── 6. Regression: other roles see full data ─────────────────────────


@pytest.mark.asyncio
async def test_owner_conversations_unmasked():
    with patch("app.db.supabase.get_conversations", new_callable=AsyncMock, return_value=([_row()], 1)):
        _as("owner", "o-1")
        async with _client() as c:
            r = await c.get("/api/conversations")
    row = r.json()["data"][0]
    assert row["visitor_name"] == "Emily"
    assert row["visitor_phone"] == "+14157179051"
    assert row["visitor_email"] == "tristud2002@yahoo.com"
    assert row["metadata"]["utm_source"] == "google"


@pytest.mark.asyncio
async def test_qa_leads_unmasked():
    lead_row = {"id": "l-1", "visitor_name": "Emily", "visitor_phone": "+14157179051",
                "visitor_email": "tristud2002@yahoo.com"}
    with (
        patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=([lead_row], 1)),
        patch("app.db.supabase.get_leads_review_counts", new_callable=AsyncMock, return_value=(0, 0)),
    ):
        _as("qa", "q-1")
        async with _client() as c:
            r = await c.get("/api/leads")
    row = r.json()["data"][0]
    assert row["visitor_name"] == "Emily"
    assert row["visitor_email"] == "tristud2002@yahoo.com"


@pytest.mark.asyncio
async def test_pm_still_403_on_leads_unchanged():
    _as("project_manager", "pm-1")
    async with _client() as c:
        r = await c.get("/api/leads")
    assert r.status_code == 403


# ── 7. Consistency: same mask via list and detail ────────────────────


@pytest.mark.asyncio
async def test_mask_consistent_list_vs_detail():
    conv = _row(status="active")
    with (
        patch("app.db.supabase.get_team_ids_for_supervisor", new_callable=AsyncMock, return_value=["alpha"]),
        patch("app.db.supabase.get_conversations", new_callable=AsyncMock, return_value=([_row(status="active")], 1)),
        patch("app.db.supabase.get_conversation", new_callable=AsyncMock, return_value=conv),
    ):
        _as("supervisor", SUP)
        async with _client() as c:
            lst = await c.get("/api/conversations")
            det = await c.get("/api/conversations/c-1")
    assert lst.json()["data"][0]["visitor_email"] == det.json()["data"]["visitor_email"]
    assert lst.json()["data"][0]["visitor_phone"] == det.json()["data"]["visitor_phone"]
