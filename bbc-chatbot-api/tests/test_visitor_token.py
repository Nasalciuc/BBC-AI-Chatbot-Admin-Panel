"""PR-12 — visitor token, Turnstile, the form trap."""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app import flags
from app.security import form_trap, turnstile, visitor_token as vt
from config.settings import settings

VID = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    flags._reset_for_tests()
    vt._reset_for_tests()
    turnstile._reset_for_tests()
    form_trap._reset_for_tests()
    monkeypatch.setattr(settings, "visitor_token_secret", "test-visitor-secret-0123456789abcdef")
    yield


# ── token ─────────────────────────────────────────────────────

def test_issue_and_check_ok():
    tok = vt.issue(VID)
    assert tok and tok.startswith("v1.")
    assert vt.check(tok, VID) == "ok"


def test_missing_is_counted_then_refused_when_enforced(monkeypatch):
    assert vt.check(None, VID) == "missing"
    assert vt.VISITOR_TOKEN_HEALTH["missing"] == 1
    monkeypatch.setenv("VISITOR_TOKEN_ENFORCE", "true")
    with pytest.raises(HTTPException) as e:
        vt.check(None, VID)
    assert e.value.status_code == 401


def test_other_visitors_token_is_refused(monkeypatch):
    monkeypatch.setenv("VISITOR_TOKEN_ENFORCE", "true")
    with pytest.raises(HTTPException) as e:
        vt.check(vt.issue(OTHER), VID)
    assert e.value.status_code == 401
    assert vt.VISITOR_TOKEN_HEALTH["mismatch"] == 1


def test_expired_is_refused(monkeypatch):
    monkeypatch.setenv("VISITOR_TOKEN_ENFORCE", "true")
    old = vt.issue(VID, now=1_000_000)
    with pytest.raises(HTTPException):
        vt.check(old, VID)
    assert vt.VISITOR_TOKEN_HEALTH["expired"] == 1


def test_forged_signature_is_invalid(monkeypatch):
    tok = vt.issue(VID)
    forged = tok[:-4] + ("0000" if not tok.endswith("0000") else "1111")
    assert vt.check(forged, VID) == "invalid"
    # A token minted with another secret is invalid too.
    monkeypatch.setattr(settings, "visitor_token_secret", "another-secret-entirely-xxxxxxxx")
    assert vt.check(tok, VID) == "invalid"


def test_no_secret_disables_everything(monkeypatch):
    monkeypatch.setattr(settings, "visitor_token_secret", "")
    monkeypatch.setenv("VISITOR_TOKEN_ENFORCE", "true")
    assert vt.issue(VID) is None
    assert vt.check(None, VID) == "disabled"


def test_expired_token_trades_in_within_grace():
    old = vt.issue(VID, now=10_000)
    vid, reason = vt.parse(old, now=10_000 + vt.TOKEN_TTL + 3600, grace=vt.REFRESH_GRACE)
    assert (vid, reason) == (VID, "ok")
    vid, reason = vt.parse(old, now=10_000 + vt.TOKEN_TTL + vt.REFRESH_GRACE + 1, grace=vt.REFRESH_GRACE)
    assert reason == "expired"


@pytest.mark.asyncio
async def test_ownership_dependency_checks_the_token(monkeypatch):
    from app.deps import ownership

    monkeypatch.setenv("VISITOR_TOKEN_ENFORCE", "true")
    with patch.object(ownership.db, "get_conversation_simple", new=AsyncMock(return_value={"visitor_id": VID})):
        await ownership.require_visitor_ownership("c1", VID, None, vt.issue(VID), None)
        with pytest.raises(HTTPException) as e:
            await ownership.require_visitor_ownership("c1", VID, None, None, None)
        assert e.value.status_code == 401
        # SSE: the token as ?vt=
        await ownership.require_visitor_ownership("c1", None, VID, None, vt.issue(VID))


# ── Turnstile ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_turnstile_off_by_default_passes():
    await turnstile.verify_or_raise(None, "1.2.3.4")
    assert turnstile.TURNSTILE_HEALTH["checked"] == 0


@pytest.mark.asyncio
async def test_turnstile_failure_is_403(monkeypatch):
    monkeypatch.setenv("TURNSTILE_ENABLED", "true")
    monkeypatch.setattr(settings, "turnstile_secret", "ts-secret", raising=False)
    monkeypatch.setattr(turnstile, "_siteverify", AsyncMock(return_value=False))
    with pytest.raises(HTTPException) as e:
        await turnstile.verify_or_raise("tok", "1.2.3.4")
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_turnstile_unavailable_lets_visitor_through(monkeypatch):
    monkeypatch.setenv("TURNSTILE_ENABLED", "true")
    monkeypatch.setattr(settings, "turnstile_secret", "ts-secret", raising=False)
    monkeypatch.setattr(turnstile, "_siteverify", AsyncMock(return_value=None))
    await turnstile.verify_or_raise("tok", "1.2.3.4")
    assert turnstile.TURNSTILE_HEALTH["unavailable"] == 1


@pytest.mark.asyncio
async def test_turnstile_success_passes(monkeypatch):
    monkeypatch.setenv("TURNSTILE_ENABLED", "true")
    monkeypatch.setattr(settings, "turnstile_secret", "ts-secret", raising=False)
    monkeypatch.setattr(turnstile, "_siteverify", AsyncMock(return_value=True))
    await turnstile.verify_or_raise("tok", "1.2.3.4")
    assert turnstile.TURNSTILE_HEALTH["passed"] == 1


# ── form trap ─────────────────────────────────────────────────

def test_form_trap_count_mode_only_counts():
    meta: dict = {}
    assert form_trap.apply({"website": "http://spam", "elapsed_ms": 5000}, meta) == "honeypot"
    assert meta == {}
    assert form_trap.FORM_TRAP_HEALTH["honeypot"] == 1


def test_form_trap_mark_mode_marks_not_drops(monkeypatch):
    monkeypatch.setenv("FORM_TRAP_MODE", "mark")
    meta: dict = {}
    assert form_trap.apply({"website": "", "elapsed_ms": 300}, meta) == "too_fast"
    assert meta["suspect"] == "form_trap"
    assert form_trap.holds_crm(meta) is True


def test_form_trap_human_fill_passes(monkeypatch):
    monkeypatch.setenv("FORM_TRAP_MODE", "mark")
    meta: dict = {}
    assert form_trap.apply({"website": "", "elapsed_ms": 4200}, meta) is None
    assert meta == {}
    assert form_trap.holds_crm(meta) is False
    # An old widget sends nothing: not a trap.
    assert form_trap.apply(None, meta) is None


def test_form_trap_off(monkeypatch):
    monkeypatch.setenv("FORM_TRAP_MODE", "off")
    meta: dict = {}
    assert form_trap.apply({"website": "x"}, meta) is None
