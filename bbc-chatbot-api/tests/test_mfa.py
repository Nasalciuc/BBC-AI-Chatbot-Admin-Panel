"""PR-13 — the second factor for owner/admin/dev."""

import os
import sys
from unittest.mock import AsyncMock, patch

import bcrypt
import pyotp
import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

with patch("app.db.supabase.get_client"):
    from app.main import app

from app import flags
from app.security import ban_guard, ban_store, login_guard, mfa
from config.settings import settings

PW = "correct horse battery"
PW_HASH = bcrypt.hashpw(PW.encode(), bcrypt.gensalt(rounds=4)).decode()


def _user(role="owner", **kw):
    u = {
        "id": "11111111-1111-1111-1111-111111111111",
        "email": "owner@buybusinessclass.com",
        "name": "Owner",
        "role": role,
        "tunnel_scope": "all",
        "password_hash": PW_HASH,
        "is_active": True,
        "token_version": 0,
        "totp_secret_enc": None,
        "mfa_enrolled_at": None,
    }
    u.update(kw)
    return u


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    flags._reset_for_tests()
    login_guard._reset_for_tests()
    mfa._reset_for_tests()
    ban_guard._reset_for_tests()
    ban_store._reset_for_tests()
    monkeypatch.setattr("app.services.response_cache._get_redis", lambda: None)
    monkeypatch.setattr(settings, "jwt_secret", "test-jwt-secret-for-mfa-0123456789abcdef")
    monkeypatch.setattr(settings, "mfa_encryption_key", "test-mfa-key-0123456789abcdef-xyz")
    monkeypatch.setattr("app.security.mfa_store.note_login_place", AsyncMock(return_value=False))
    monkeypatch.setattr("app.security.token_version.matches", lambda *a, **k: True)
    from app.security import rate_limiter
    rate_limiter._buckets.clear()
    yield
    rate_limiter._buckets.clear()


async def _post(path, body, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.post(path, json=body, headers={"X-Forwarded-For": "7.7.7.7", **(headers or {})})


async def _login(user):
    with patch("app.api.auth_routes.db.get_user_by_email", new=AsyncMock(return_value=user)):
        return await _post("/api/auth/login", {"email": user["email"], "password": PW})


@pytest.mark.asyncio
async def test_privileged_without_mfa_gets_enrolment_only():
    r = await _login(_user())
    assert r.status_code == 200
    body = r.json()
    assert body["token"] == "" and body["mfa"] == "enroll" and body["mfa_token"]
    # The enrolment token reaches no normal route.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r2 = await c.get("/api/conversations", headers={"Authorization": f"Bearer {body['mfa_token']}"})
    assert r2.status_code == 401


@pytest.mark.asyncio
async def test_sales_logs_in_normally():
    r = await _login(_user(role="sales"))
    assert r.status_code == 200 and r.json()["token"] and r.json()["mfa"] is None


@pytest.mark.asyncio
async def test_unapplied_migration_never_locks_out():
    u = _user()
    u.pop("mfa_enrolled_at")
    u.pop("totp_secret_enc")
    r = await _login(u)
    assert r.json()["token"] and r.json()["mfa"] is None
    assert mfa.MFA_HEALTH["unavailable"] is True


@pytest.mark.asyncio
async def test_enforce_off_is_the_old_login(monkeypatch):
    monkeypatch.setenv("MFA_ENFORCE", "false")
    r = await _login(_user())
    assert r.json()["token"]


def _enrolled_user():
    secret = pyotp.random_base32()
    return secret, _user(totp_secret_enc=mfa.encrypt(secret), mfa_enrolled_at="2026-11-01T00:00:00+00:00")


@pytest.mark.asyncio
async def test_enrolled_needs_the_code_and_a_wrong_code_is_401():
    secret, u = _enrolled_user()
    r = await _login(u)
    body = r.json()
    assert body["mfa"] == "required" and body["token"] == ""
    with patch("app.api.auth_routes.db.get_user_by_id", new=AsyncMock(return_value=u)):
        bad = await _post("/api/auth/mfa", {"mfa_token": body["mfa_token"], "code": "000000"})
        assert bad.status_code == 401
        good = await _post("/api/auth/mfa", {"mfa_token": body["mfa_token"], "code": pyotp.TOTP(secret).now()})
    assert good.status_code == 200 and good.json()["token"]
    assert mfa.MFA_HEALTH["failed"] == 1 and mfa.MFA_HEALTH["passed"] == 1


@pytest.mark.asyncio
async def test_recovery_code_works_once():
    secret, u = _enrolled_user()
    code = "abcde-fghjk"
    row = {"id": "r1", "code_hash": mfa.hash_recovery(code)}
    used = {"n": 0}

    async def _use(_id):
        used["n"] += 1
        return used["n"] == 1  # the conditional update matches only the first time

    body = (await _login(u)).json()
    with patch("app.api.auth_routes.db.get_user_by_id", new=AsyncMock(return_value=u)), \
         patch("app.security.mfa_store.unused_recovery_codes", new=AsyncMock(return_value=[row])), \
         patch("app.security.mfa_store.use_recovery_code", new=_use):
        first = await _post("/api/auth/mfa", {"mfa_token": body["mfa_token"], "code": code})
        second = await _post("/api/auth/mfa", {"mfa_token": body["mfa_token"], "code": code})
    assert first.status_code == 200
    assert second.status_code == 401
    assert mfa.MFA_HEALTH["recovery_used"] == 1


@pytest.mark.asyncio
async def test_enrolment_start_and_confirm_returns_codes_and_session():
    u = _user()
    body = (await _login(u)).json()
    stored = {}

    async def _set_pending(uid, enc):
        stored["enc"] = enc
        return True

    with patch("app.api.auth_routes.db.get_user_by_id", new=AsyncMock(return_value=u)), \
         patch("app.security.mfa_store.set_pending_secret", new=_set_pending):
        r = await _post("/api/auth/mfa/enroll/start", {"mfa_token": body["mfa_token"]})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["otpauth_uri"].startswith("otpauth://totp/")
    assert "<svg" in data["qr_svg"]
    secret = mfa.decrypt(stored["enc"])
    assert secret and secret not in stored["enc"]  # stored encrypted

    u2 = dict(u, totp_secret_enc=stored["enc"])
    with patch("app.api.auth_routes.db.get_user_by_id", new=AsyncMock(return_value=u2)), \
         patch("app.security.mfa_store.replace_recovery_codes", new=AsyncMock(return_value=True)), \
         patch("app.security.mfa_store.mark_enrolled", new=AsyncMock(return_value=True)):
        c = await _post("/api/auth/mfa/enroll/confirm",
                        {"mfa_token": body["mfa_token"], "code": pyotp.TOTP(secret).now()})
    assert c.status_code == 200
    out = c.json()["data"]
    assert len(out["recovery_codes"]) == 10 and out["token"]


@pytest.mark.asyncio
async def test_already_enrolled_cannot_re_enrol_in_place():
    _, u = _enrolled_user()
    tok = mfa.issue_limited(u, mfa.SCOPE_ENROLL)
    with patch("app.api.auth_routes.db.get_user_by_id", new=AsyncMock(return_value=u)):
        r = await _post("/api/auth/mfa/enroll/start", {"mfa_token": tok})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_pending_token_cannot_enrol_and_enrol_token_cannot_finish_login():
    _, u = _enrolled_user()
    pending = mfa.issue_limited(u, mfa.SCOPE_PENDING)
    enroll = mfa.issue_limited(_user(), mfa.SCOPE_ENROLL)
    with patch("app.api.auth_routes.db.get_user_by_id", new=AsyncMock(return_value=u)):
        r1 = await _post("/api/auth/mfa/enroll/start", {"mfa_token": pending})
        r2 = await _post("/api/auth/mfa", {"mfa_token": enroll, "code": "123456"})
    assert r1.status_code == 401 and r2.status_code == 401


def test_limited_token_is_not_staff_for_fail2ban():
    from app.security.ban_guard import _has_valid_jwt
    tok = mfa.issue_limited(_user(), mfa.SCOPE_PENDING)
    assert _has_valid_jwt({"authorization": f"Bearer {tok}"}) is False


def test_sso_path_is_untouched():
    import inspect
    from app.api import auth_routes
    src = inspect.getsource(auth_routes.crm_sso_exchange)
    assert "mfa" not in src


@pytest.mark.asyncio
async def test_weekly_security_report_counts_without_pii(monkeypatch):
    from app.api import cron

    monkeypatch.setattr(cron, "_require_cron", lambda r: None)
    sent = {}

    async def _send(subject, body, **kw):
        sent["subject"], sent["body"] = subject, body
        return True

    async def _rows(fn, **kw):
        class R:
            data = [{"jail": "honeypot"}, {"jail": "honeypot"}, {"jail": "scan"}]
        return R()

    monkeypatch.setattr("app.services.email.send_ops_alert_email", _send)
    monkeypatch.setattr(cron.db, "_run_sync", _rows)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/cron/security-weekly")
    assert r.status_code == 200
    assert r.json()["data"]["bans_by_jail"] == {"honeypot": 2, "scan": 1}
    assert "honeypot=2" in sent["body"] and "@" not in sent["body"]
