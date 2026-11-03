"""POST /api/auth/login — throttles the email+IP pair, same 429 either way."""

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

from app import flags
from app.security import login_guard


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    flags._reset_for_tests()
    login_guard._reset_for_tests()
    monkeypatch.setattr("app.services.response_cache._get_redis", lambda: None)
    yield
    login_guard._reset_for_tests()


async def _post(email, ip="7.7.7.7"):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.post(
            "/api/auth/login",
            json={"email": email, "password": "wrong"},
            headers={"X-Forwarded-For": ip},
        )


@pytest.mark.asyncio
async def test_unknown_and_known_email_look_the_same_then_throttle():
    with patch("app.api.auth_routes.db.get_user_by_email", new=AsyncMock(return_value=None)):
        for _ in range(5):
            r = await _post("ghost@bbc.com")
            assert r.status_code == 401
        r = await _post("ghost@bbc.com")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


@pytest.mark.asyncio
async def test_throttle_is_per_ip():
    with patch("app.api.auth_routes.db.get_user_by_email", new=AsyncMock(return_value=None)):
        for _ in range(6):
            await _post("x@bbc.com", ip="1.2.3.4")
        # a different address for the same email is still free
        r = await _post("x@bbc.com", ip="5.6.7.8")
    assert r.status_code == 401
