"""CRM SSO exchange — POST /api/auth/sso/crm-exchange (docs/chatbot-sso.md).

The CRM signs a short-lived JWT (HS256, CHAT_SSO_SECRET, iss=crm) and passes it via
?token=. We verify it, look up OUR user by email, and mint OUR own session JWT.
role/tunnel_scope always come from OUR db — never from the CRM token.

Covers: 503 unconfigured; 400 when email claim missing (expected until CRM adds it);
401 generic for unknown email (no enumeration); 200 valid → role/scope from DB even
when the CRM token carries a bogus role; 401 wrong issuer; 401 expired; 401 wrong
secret; 403 inactive.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import jwt as _jwt
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth_routes
from app.api.auth_routes import router
from app.security.rate_limiter import check_rate_limit
from config.settings import settings

_CHAT_SSO = "test-chat-sso-secret-shared-with-crm"
_JWT = "test-jwt-secret-ours-only"  # distinct from the CRM secret

_ACTIVE_USER = {
    "id": "op-1",
    "email": "operator@buybusinessclass.com",
    "name": "Ops Person",
    "role": "sales",
    "tunnel_scope": "sales",
    "phone": "+15551230000",
    "avatar_url": None,
    "is_active": True,
}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    # No-op the per-IP limiter so repeated test calls don't hit the burst cap.
    app.dependency_overrides[check_rate_limit] = lambda: None
    return TestClient(app)


def _crm_token(secret=_CHAT_SSO, **overrides) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "iss": "crm",
        "sub": "123",
        "name": "John Doe",
        "iat": now,
        "nbf": now - timedelta(seconds=5),
        "exp": now + timedelta(seconds=900),
    }
    payload.update(overrides)
    return _jwt.encode(payload, secret, algorithm="HS256")


def _post(client, token):
    return client.post("/api/auth/sso/crm-exchange", json={"token": token})


# ── 1. Not configured → 503 ─────────────────────────────────────────


def test_not_configured_503(client):
    with patch.object(settings, "chat_sso_secret", ""):
        resp = _post(client, _crm_token())
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"].lower()


# ── 2. Valid token WITHOUT email → 400 clear message (expected today) ─


def test_missing_email_400(client):
    with (
        patch.object(settings, "chat_sso_secret", _CHAT_SSO),
        patch.object(settings, "jwt_secret", _JWT),
    ):
        resp = _post(client, _crm_token())  # no email claim
    assert resp.status_code == 400
    assert "email" in resp.json()["detail"].lower()


# ── 3. Valid token WITH unknown email → 401 generic (not 404) ───────


def test_unknown_email_generic_401(client):
    with (
        patch.object(settings, "chat_sso_secret", _CHAT_SSO),
        patch.object(settings, "jwt_secret", _JWT),
        patch.object(auth_routes.db, "get_user_by_email", AsyncMock(return_value=None)),
    ):
        resp = _post(client, _crm_token(email="ghost@buybusinessclass.com"))
    assert resp.status_code == 401
    assert resp.status_code != 404
    assert "not found" not in resp.json()["detail"].lower()


# ── 4. Valid token WITH known active email → 200, role/scope from DB ─


def test_valid_exchange_role_comes_from_db(client):
    with (
        patch.object(settings, "chat_sso_secret", _CHAT_SSO),
        patch.object(settings, "jwt_secret", _JWT),
        patch.object(auth_routes.db, "get_user_by_email", AsyncMock(return_value=_ACTIVE_USER)),
    ):
        # CRM token deliberately claims role=owner + a different scope — must be IGNORED.
        token = _crm_token(
            email="operator@buybusinessclass.com",
            role="owner",
            tunnel_scope="all",
        )
        resp = _post(client, token)
    assert resp.status_code == 200
    body = resp.json()
    claims = _jwt.decode(body["token"], _JWT, algorithms=["HS256"])
    # role/scope come from OUR db (sales/sales), NOT the CRM payload (owner/all)
    assert claims["role"] == "sales"
    assert claims["tunnel_scope"] == "sales"
    assert claims["email"] == "operator@buybusinessclass.com"
    assert claims["sub"] == "op-1"  # our user id, not the CRM sub "123"
    assert body["user"]["role"] == "sales"
    assert body["user"]["id"] == "op-1"


def test_email_is_normalized(client):
    seen = {}

    async def _capture(email):
        seen["email"] = email
        return _ACTIVE_USER

    with (
        patch.object(settings, "chat_sso_secret", _CHAT_SSO),
        patch.object(settings, "jwt_secret", _JWT),
        patch.object(auth_routes.db, "get_user_by_email", _capture),
    ):
        resp = _post(client, _crm_token(email="  Operator@BuyBusinessClass.com  "))
    assert resp.status_code == 200
    assert seen["email"] == "operator@buybusinessclass.com"


# ── 5. Wrong issuer → 401 ───────────────────────────────────────────


def test_wrong_issuer_401(client):
    with (
        patch.object(settings, "chat_sso_secret", _CHAT_SSO),
        patch.object(settings, "jwt_secret", _JWT),
    ):
        resp = _post(client, _crm_token(iss="evil", email="operator@buybusinessclass.com"))
    assert resp.status_code == 401
    assert "issuer" in resp.json()["detail"].lower()


# ── 6. Expired token → 401 expired ──────────────────────────────────


def test_expired_token_401(client):
    now = datetime.now(timezone.utc)
    with (
        patch.object(settings, "chat_sso_secret", _CHAT_SSO),
        patch.object(settings, "jwt_secret", _JWT),
    ):
        token = _crm_token(
            iat=now - timedelta(seconds=920),
            nbf=now - timedelta(seconds=925),
            exp=now - timedelta(seconds=10),
            email="operator@buybusinessclass.com",
        )
        resp = _post(client, token)
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


# ── 7. Wrong signing secret → 401 ───────────────────────────────────


def test_wrong_secret_401(client):
    with (
        patch.object(settings, "chat_sso_secret", _CHAT_SSO),
        patch.object(settings, "jwt_secret", _JWT),
    ):
        token = _crm_token(secret="not-the-shared-secret", email="operator@buybusinessclass.com")
        resp = _post(client, token)
    assert resp.status_code == 401
    assert "invalid sso token" in resp.json()["detail"].lower()


def test_immature_nbf_401(client):
    # nbf in the future → PyJWT ImmatureSignatureError (subclass of InvalidTokenError)
    now = datetime.now(timezone.utc)
    with (
        patch.object(settings, "chat_sso_secret", _CHAT_SSO),
        patch.object(settings, "jwt_secret", _JWT),
    ):
        token = _crm_token(
            iat=now,
            nbf=now + timedelta(seconds=120),
            exp=now + timedelta(seconds=900),
            email="operator@buybusinessclass.com",
        )
        resp = _post(client, token)
    assert resp.status_code == 401


# ── 8. Inactive user → 403 ──────────────────────────────────────────


def test_inactive_user_403(client):
    inactive = {**_ACTIVE_USER, "is_active": False}
    with (
        patch.object(settings, "chat_sso_secret", _CHAT_SSO),
        patch.object(settings, "jwt_secret", _JWT),
        patch.object(auth_routes.db, "get_user_by_email", AsyncMock(return_value=inactive)),
    ):
        resp = _post(client, _crm_token(email="operator@buybusinessclass.com"))
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"].lower()


# ── secrets are distinct concepts ───────────────────────────────────


def test_chat_sso_secret_distinct_from_jwt_secret():
    with (
        patch.object(settings, "chat_sso_secret", _CHAT_SSO),
        patch.object(settings, "jwt_secret", _JWT),
    ):
        assert settings.chat_sso_secret != settings.jwt_secret
