"""Auth hardening — JWT is the only real user path; unsafe fallbacks removed.

Covers: valid JWT keeps its role/scope (not owner); Bearer==API_PASS is
rejected; fail-closed 500 when no jwt_secret in prod; explicit debug bypass;
expired/malformed 401; and the guardrail that login-issued tokens still work.
"""

import base64
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt as _jwt
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from fastapi import HTTPException

from app.security.auth import get_current_user
from config.settings import settings

_SECRET = "test-jwt-secret-please-ignore"


def _req(auth: str | None = None) -> MagicMock:
    r = MagicMock()
    r.headers = {"Authorization": auth} if auth else {}
    r.url = MagicMock()
    r.url.path = "/api/leads"
    # MagicMock().headers.get works, but we want a real dict lookup:
    r.headers = {} if auth is None else {"Authorization": auth}
    return r


def _make_token(secret=_SECRET, **claims) -> str:
    payload = {
        "sub": "user-1",
        "email": "sales@bbc.com",
        "name": "Sales Person",
        "role": "sales",
        "tunnel_scope": "sales",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    payload.update(claims)
    return _jwt.encode(payload, secret, algorithm="HS256")


# ── 1. Valid JWT keeps its role/scope (never defaulted to owner) ─────


def test_valid_jwt_returns_role_not_owner():
    with (
        patch.object(settings, "debug", False),
        patch.object(settings, "jwt_secret", _SECRET),
    ):
        token = _make_token(role="sales", tunnel_scope="sales")
        user = get_current_user(_req(f"Bearer {token}"))
    assert user["role"] == "sales"
    assert user["tunnel_scope"] == "sales"
    assert user["id"] == "user-1"
    assert user["role"] != "owner"


def test_valid_jwt_qa_role_preserved():
    with (
        patch.object(settings, "debug", False),
        patch.object(settings, "jwt_secret", _SECRET),
    ):
        token = _make_token(role="qa", tunnel_scope="all", sub="qa-1")
        user = get_current_user(_req(f"Bearer {token}"))
    assert user["role"] == "qa"
    assert user["tunnel_scope"] == "all"


# ── 2. Bearer == API_PASS but not a valid JWT → 401 (fallback removed) ─


def test_bearer_equal_api_pass_is_rejected():
    with (
        patch.object(settings, "debug", False),
        patch.object(settings, "jwt_secret", _SECRET),
        patch.object(settings, "api_pass", "super-secret-pass"),
        patch.object(settings, "api_user", "admin"),
    ):
        with pytest.raises(HTTPException) as exc:
            get_current_user(_req("Bearer super-secret-pass"))
    assert exc.value.status_code == 401
    assert "Invalid token" in exc.value.detail


# ── 3. No jwt_secret + debug=False + Bearer → 500 fail-closed ────────


def test_no_secret_prod_bearer_fails_closed_500():
    with (
        patch.object(settings, "debug", False),
        patch.object(settings, "jwt_secret", ""),
    ):
        with pytest.raises(HTTPException) as exc:
            get_current_user(_req("Bearer anything"))
    assert exc.value.status_code == 500
    assert "misconfigured" in exc.value.detail.lower()


# ── 4. debug=True + no jwt_secret → explicit dev bypass (owner) ──────


def test_debug_bypass_returns_owner():
    with (
        patch.object(settings, "debug", True),
        patch.object(settings, "jwt_secret", ""),
    ):
        user = get_current_user(_req(None))
    assert user["role"] == "owner"
    assert user["tunnel_scope"] == "all"


def test_debug_bypass_inactive_when_secret_set():
    # debug True but secret present → NOT a bypass; missing auth → 401
    with (
        patch.object(settings, "debug", True),
        patch.object(settings, "jwt_secret", _SECRET),
    ):
        with pytest.raises(HTTPException) as exc:
            get_current_user(_req(None))
    assert exc.value.status_code == 401


# ── 5. Expired → 401 expired; malformed → 401 invalid ───────────────


def test_expired_jwt_401():
    with (
        patch.object(settings, "debug", False),
        patch.object(settings, "jwt_secret", _SECRET),
    ):
        token = _make_token(exp=datetime.now(timezone.utc) - timedelta(hours=1))
        with pytest.raises(HTTPException) as exc:
            get_current_user(_req(f"Bearer {token}"))
    assert exc.value.status_code == 401
    assert "expired" in exc.value.detail.lower()


def test_malformed_jwt_401():
    with (
        patch.object(settings, "debug", False),
        patch.object(settings, "jwt_secret", _SECRET),
    ):
        with pytest.raises(HTTPException) as exc:
            get_current_user(_req("Bearer not.a.jwt"))
    assert exc.value.status_code == 401
    assert "invalid" in exc.value.detail.lower()


def test_wrong_secret_jwt_401():
    with (
        patch.object(settings, "debug", False),
        patch.object(settings, "jwt_secret", _SECRET),
    ):
        token = _make_token(secret="a-different-secret")
        with pytest.raises(HTTPException) as exc:
            get_current_user(_req(f"Bearer {token}"))
    assert exc.value.status_code == 401


# ── 6. Guardrail: login-issued token still passes get_current_user ──


def test_login_issued_token_still_works():
    from app.api.auth_routes import _issue_jwt

    with (
        patch.object(settings, "debug", False),
        patch.object(settings, "jwt_secret", _SECRET),
    ):
        token = _issue_jwt({
            "id": "u-42",
            "email": "steve@bbc.com",
            "name": "Steve Holt",
            "role": "sales",
            "tunnel_scope": "sales",
            "phone": "+15551230000",
        })
        user = get_current_user(_req(f"Bearer {token}"))
    assert user["id"] == "u-42"
    assert user["email"] == "steve@bbc.com"
    assert user["role"] == "sales"
    assert user["tunnel_scope"] == "sales"


# ── Basic Auth escape hatch still works (no lockout of ops) ─────────


def test_basic_auth_escape_hatch_still_works():
    with (
        patch.object(settings, "debug", False),
        patch.object(settings, "jwt_secret", _SECRET),
        patch.object(settings, "api_user", "admin"),
        patch.object(settings, "api_pass", "secret"),
    ):
        creds = base64.b64encode(b"admin:secret").decode()
        user = get_current_user(_req(f"Basic {creds}"))
    assert user["role"] == "owner"
    assert user["name"] == "admin"


def test_no_auth_header_401():
    with (
        patch.object(settings, "debug", False),
        patch.object(settings, "jwt_secret", _SECRET),
    ):
        with pytest.raises(HTTPException) as exc:
            get_current_user(_req(None))
    assert exc.value.status_code == 401
