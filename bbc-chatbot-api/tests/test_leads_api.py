"""Tests for GET /api/leads — admin leads list endpoint."""

import sys
import os
from unittest.mock import patch, AsyncMock
import base64
import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
# Auth enabled: set credentials so 401 test works
os.environ["API_USER"] = "testadmin"
os.environ["API_PASS"] = "testpass"

# Mock supabase client before app import
with patch("app.db.supabase.get_client"):
    from app.main import app

# ── Auth header ──────────────────────────────────────────────
AUTH_HEADERS = {
    "Authorization": "Basic " + base64.b64encode(b"testadmin:testpass").decode()
}

# ── Sample data ──────────────────────────────────────────────
LEAD_NEW_GOLD = {
    "id": "lead-001",
    "conversation_id": "conv-001",
    "trip_type": "round_trip",
    "cabin_class": "business",
    "passengers": 2,
    "flexible_dates": True,
    "origin_code": "JFK",
    "destination_code": "LHR",
    "departure_date": "2026-04-15",
    "return_date": "2026-04-22",
    "route_display": "JFK \u2192 LHR",
    "score": 85,
    "tier": "gold",
    "status": "new",
    "intent_signals": ["asked_price", "gave_dates"],
    "notes": "",
    "created_at": "2026-03-15T10:00:00Z",
    "updated_at": "2026-03-15T10:00:00Z",
    "contacted_at": None,
    "converted_at": None,
    "visitor_name": "John Smith",
    "visitor_email": "john@example.com",
    "visitor_phone": "+1234567890",
}

LEAD_CONTACTED_SILVER = {
    **LEAD_NEW_GOLD,
    "id": "lead-002",
    "conversation_id": "conv-002",
    "score": 55,
    "tier": "silver",
    "status": "contacted",
    "visitor_name": "Jane Doe",
    "visitor_email": "jane@example.com",
}

ALL_LEADS = [LEAD_NEW_GOLD, LEAD_CONTACTED_SILVER]


# ── Tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_leads_200():
    """GET /api/leads returns 200 with shape {success: true, data: [...], count: N}."""
    with patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=(ALL_LEADS, 2)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/leads", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert len(body["data"]) == 2
    assert body["count"] == 2


@pytest.mark.asyncio
async def test_list_leads_pagination():
    """limit=5&offset=0 are forwarded to DB correctly."""
    with patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=([LEAD_NEW_GOLD], 50)) as mock_db:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/leads?limit=5&offset=0", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["count"] == 50
    mock_db.assert_called_once_with(
        status=None, tier=None, tunnel=None, search=None,
        assigned_to="all", include_drafts=False, reviewed_filter=None,
        limit=5, offset=0,
    )


@pytest.mark.asyncio
async def test_list_leads_filter_status():
    """?status=new filters to only new leads."""
    with patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=([LEAD_NEW_GOLD], 1)) as mock_db:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/leads?status=new", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert all(lead["status"] == "new" for lead in body["data"])
    mock_db.assert_called_once_with(
        status="new", tier=None, tunnel=None, search=None,
        assigned_to="all", include_drafts=False, reviewed_filter=None,
        limit=50, offset=0,
    )


@pytest.mark.asyncio
async def test_list_leads_filter_tier():
    """?tier=gold filters to only gold leads."""
    with patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=([LEAD_NEW_GOLD], 1)) as mock_db:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/leads?tier=gold", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert all(lead["tier"] == "gold" for lead in body["data"])
    mock_db.assert_called_once_with(
        status=None, tier="gold", tunnel=None, search=None,
        assigned_to="all", include_drafts=False, reviewed_filter=None,
        limit=50, offset=0,
    )


@pytest.mark.asyncio
async def test_list_leads_search():
    """?search=john matches visitor name/email."""
    with patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=([LEAD_NEW_GOLD], 1)) as mock_db:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/leads?search=john", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"][0]["visitor_name"] == "John Smith"
    mock_db.assert_called_once_with(
        status=None, tier=None, tunnel=None, search="john",
        assigned_to="all", include_drafts=False, reviewed_filter=None,
        limit=50, offset=0,
    )


@pytest.mark.asyncio
async def test_list_leads_401():
    """No auth header returns 401."""
    with patch("config.settings.settings.api_user", "admin"), \
         patch("config.settings.settings.api_pass", "secret"), \
         patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=([], 0)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/leads")  # no headers
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_leads_invalid_params():
    """tier=invalid returns 422 validation error."""
    with patch("app.db.supabase.get_leads", new_callable=AsyncMock, return_value=([], 0)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/leads?tier=invalid", headers=AUTH_HEADERS)
    assert r.status_code == 422
