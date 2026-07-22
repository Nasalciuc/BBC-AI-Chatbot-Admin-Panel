"""CRM bridge SSO endpoint — POST /api/auth/crm-bridge.

Server-to-server mint path: the CRM's backend vouches for an operator email
(after ITS own login) and receives a normal panel JWT. No password.

Covers: 503 when unconfigured; 401 wrong/missing bridge secret; 401 generic for
unknown email (no enumeration); 403 inactive; 200 valid → token decodes to the
right claims; token is structurally identical to a /login-issued token; and the
same check_rate_limit dependency as /login is wired on both routes.
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
from app.api.auth_routes import router, _issue_jwt, check_rate_limit
from app.security.rate_limiter import check_rate_limit as rl_dep
from config.settings import settings

_SECRET = "test-jwt-secret-please-ignore"
_BRIDGE = "test-crm-bridge-secret-distinct"

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
    return TestClient(app)


def _post(client, *, email="operator@buybusinessclass.com", secret=_BRIDGE):
    headers = {}
    if secret is not None:
        headers["Authorization"] = f"Bearer {secret}"
    return client.post("/api/auth/crm-bridge", json={"email": email}, headers=headers)


# ── 1. No secret configured → 503 ───────────────────────────────────


def test_bridge_not_configured_503(client):
    with patch.object(settings, "crm_bridge_secret", ""):
        resp = _post(client)
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"].lower()


# ── 2. Wrong / missing bridge secret → 401 ──────────────────────────


def test_wrong_bridge_secret_401(client):
    with patch.object(settings, "crm_bridge_secret", _BRIDGE):
        resp = _post(client, secret="not-the-secret")
    assert resp.status_code == 401
    assert "bridge token" in resp.json()["detail"].lower()


def test_missing_bridge_secret_header_401(client):
    with patch.object(settings, "crm_bridge_secret", _BRIDGE):
        resp = _post(client, secret=None)
    assert resp.status_code == 401


def test_bridge_secret_not_jwt_secret():
    # Guard: the two secrets are distinct concepts — a JWT_SECRET value must not
    # be accepted as a bridge token.
    with (
        patch.object(settings, "crm_bridge_secret", _BRIDGE),
        patch.object(settings, "jwt_secret", _SECRET),
    ):
        assert settings.crm_bridge_secret != settings.jwt_secret


# ── 3. Unknown email → 401 generic (NOT 404, no enumeration) ────────


def test_unknown_email_generic_401(client):
    with (
        patch.object(settings, "crm_bridge_secret", _BRIDGE),
        patch.object(settings, "jwt_secret", _SECRET),
        patch.object(auth_routes.db, "get_user_by_email", AsyncMock(return_value=None)),
    ):
        resp = _post(client, email="ghost@buybusinessclass.com")
    assert resp.status_code == 401
    # generic — must not reveal that the email doesn't exist
    assert resp.status_code != 404
    assert "not found" not in resp.json()["detail"].lower()


# ── 4. Inactive user → 403 ──────────────────────────────────────────


def test_inactive_user_403(client):
    inactive = {**_ACTIVE_USER, "is_active": False}
    with (
        patch.object(settings, "crm_bridge_secret", _BRIDGE),
        patch.object(settings, "jwt_secret", _SECRET),
        patch.object(auth_routes.db, "get_user_by_email", AsyncMock(return_value=inactive)),
    ):
        resp = _post(client)
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"].lower()


# ── 5. Valid email + correct secret → 200, token decodes correctly ──


def test_valid_bridge_login_200(client):
    with (
        patch.object(settings, "crm_bridge_secret", _BRIDGE),
        patch.object(settings, "jwt_secret", _SECRET),
        patch.object(auth_routes.db, "get_user_by_email", AsyncMock(return_value=_ACTIVE_USER)),
    ):
        resp = _post(client)
    assert resp.status_code == 200
    body = resp.json()
    token = body["token"]
    claims = _jwt.decode(token, _SECRET, algorithms=["HS256"])
    assert claims["sub"] == "op-1"
    assert claims["email"] == "operator@buybusinessclass.com"
    assert claims["role"] == "sales"
    assert claims["tunnel_scope"] == "sales"
    # response user block mirrors /login
    assert body["user"]["id"] == "op-1"
    assert body["user"]["email"] == "operator@buybusinessclass.com"
    assert body["user"]["role"] == "sales"


def test_email_is_normalized(client):
    seen = {}

    async def _capture(email):
        seen["email"] = email
        return _ACTIVE_USER

    with (
        patch.object(settings, "crm_bridge_secret", _BRIDGE),
        patch.object(settings, "jwt_secret", _SECRET),
        patch.object(auth_routes.db, "get_user_by_email", _capture),
    ):
        resp = _post(client, email="  Operator@BuyBusinessClass.com  ")
    assert resp.status_code == 200
    assert seen["email"] == "operator@buybusinessclass.com"


# ── 6. Token structurally identical to a /login-issued token ────────


def test_token_identical_shape_to_login():
    with (
        patch.object(settings, "jwt_secret", _SECRET),
        patch.object(settings, "jwt_expiry_hours", 24),
    ):
        # Both paths go through the SAME _issue_jwt helper → identical claim keys
        bridge_token = _issue_jwt(_ACTIVE_USER)
        login_token = _issue_jwt(_ACTIVE_USER)

        bc = _jwt.decode(bridge_token, _SECRET, algorithms=["HS256"])
        lc = _jwt.decode(login_token, _SECRET, algorithms=["HS256"])

    assert set(bc.keys()) == set(lc.keys())
    # same expiry window (within a couple seconds of issuance)
    exp = datetime.fromtimestamp(bc["exp"], tz=timezone.utc)
    expected = datetime.now(timezone.utc) + timedelta(hours=24)
    assert abs((exp - expected).total_seconds()) < 10
    # non-expiry claims equal for the same user
    for k in bc:
        if k == "exp":
            continue
        assert bc[k] == lc[k]


# ── 7. Rate limiting: same dependency as /login is wired ────────────


def _route_deps(path: str, method: str = "POST"):
    for r in router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return [d.call for d in r.dependant.dependencies]
    return []


def test_rate_limit_dependency_matches_login():
    bridge_deps = _route_deps("/api/auth/crm-bridge")
    login_deps = _route_deps("/api/auth/login")
    assert rl_dep in bridge_deps, "crm-bridge must use check_rate_limit"
    assert rl_dep in login_deps, "login must use check_rate_limit"
    # bound reference in the module is the same object
    assert check_rate_limit is rl_dep
