"""Quiet when nothing happened; the right level when something did.

9 Nov, Railway: agent_sweep wrote `swept=0` twice every 15 s, abandoned_crm
printed 200 conversation ids every 5 minutes, every CRM page wrote an SSO
INFO line, and a gate refusal (a lead with a typo in its email domain) was an
ERROR every 5 minutes for days — 53 of the 217 ERROR lines of the morning.
"""

import base64
import json
import logging
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

def _placeholder_jwt() -> str:
    """JWT-shaped, built at run time: create_client checks the shape and
    nothing checks the signature, and no key-shaped literal sits in the
    source for the secret scan (.gitleaks.toml) to flag."""
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part({'role': 'test'})}.sig"


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", _placeholder_jwt())
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.services import scheduler as sched


@pytest.fixture(autouse=True)
def _fresh_quiet_state():
    sched._was_quiet.clear()
    yield
    sched._was_quiet.clear()


# ── the scheduler's line per run ─────────────────────────────────────────


def _levels(job, results):
    out = []
    for r in results:
        out.append("DEBUG" if sched._is_quiet(job, sched._loggable(job, r)) else "INFO")
    return out


def test_a_sweep_that_swept_nothing_is_written_once_then_on_every_change():
    runs = [{"success": True, "swept": n} for n in (0, 0, 0, 2, 0, 0)]
    assert _levels("agent_sweep", runs) == ["INFO", "DEBUG", "DEBUG", "INFO", "INFO", "DEBUG"]


@pytest.mark.parametrize("job,quiet,busy", [
    ("queue_stall_alert", {"state": "ok", "waiting": 0}, {"state": "stalled", "waiting": 2}),
    ("db_saturation_alert", {"state": "ok", "in_flight": 3}, {"state": "over", "in_flight": 19}),
    ("stale_ready", {"reset": 0, "names": []}, {"reset": 1, "names": ["x"]}),
    ("stale_presence", {"closed": 0}, {"closed": 2}),
    ("attention_emails", {"sent": 0, "skipped": 3}, {"sent": 1, "skipped": 0}),
])
def test_every_quiet_rule_lets_the_busy_run_and_the_recovery_through(job, quiet, busy):
    assert _levels(job, [quiet, quiet, busy, quiet, quiet]) == ["INFO", "DEBUG", "INFO", "INFO", "DEBUG"]


def test_a_job_without_a_rule_is_always_written():
    assert _levels("ip_selftest", [{"ok": True}] * 3) == ["INFO"] * 3


class _Unreadable:
    def __bool__(self):
        raise RuntimeError("a result the rule cannot read")


def test_a_rule_that_breaks_writes_the_run_in_full():
    assert _levels("agent_sweep", ["not a dict", {"swept": _Unreadable()}]) == ["INFO", "INFO"]


def _sweep(*statuses, success=0):
    return {
        "processed": len(statuses),
        "success": success,
        "results": [{"id": f"{i:08x}-0000-0000-0000-000000000000", "status": s}
                    for i, s in enumerate(statuses)],
    }


def test_the_abandoned_sweep_shows_counts_not_two_hundred_ids():
    shown = sched._loggable("abandoned_crm", _sweep(*(["already_done"] * 199), "crm_failed"))
    assert shown["by_status"] == {"already_done": 199, "crm_failed": 1}
    assert shown["not_done"] == ["000000c7"]
    assert "results" not in shown
    assert len(str(shown)) < 200


def test_the_abandoned_sweep_is_quiet_only_while_nothing_changes():
    same_refusal = _sweep("already_done", "crm_failed")
    pushed = _sweep("success", "crm_failed", success=1)
    new_refusal = _sweep("crm_failed", "crm_failed")
    assert _levels("abandoned_crm", [same_refusal, same_refusal, pushed, new_refusal, new_refusal]) == [
        "INFO", "DEBUG", "INFO", "INFO", "DEBUG",
    ]


@pytest.mark.asyncio
async def test_the_loop_writes_the_compact_line(caplog):
    import asyncio

    async def job():
        return _sweep(*(["already_done"] * 5), "crm_failed")

    with patch("app.services.scheduler.random.uniform", return_value=0), caplog.at_level(logging.DEBUG):
        task = asyncio.create_task(sched._run_forever("abandoned_crm", job, 0.01))
        await asyncio.sleep(0.08)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    lines = [r for r in caplog.records if "[scheduler:internal] abandoned_crm:" in r.getMessage()]
    assert lines and lines[0].levelno == logging.INFO
    assert all(r.levelno == logging.DEBUG for r in lines[1:]), "the same state again is DEBUG"
    assert "'by_status'" in lines[0].getMessage() and "'results'" not in lines[0].getMessage()
    assert sched.last_runs["abandoned_crm"]["result"]["results"], "/health keeps the full result"


# ── the abandoned sweep: a gate refusal is a WARNING ─────────────────────

from app.api import cron  # noqa: E402

_CID = "conv-quiet-1"


def _conv():
    return {
        "id": _CID, "status": "active", "tunnel": "sales",
        "visitor_name": "Chris", "visitor_email": "c@x.com",
        "visitor_phone": "+18280000000", "chat_number": 1373,
        "visitor_id": "v1", "metadata": {}, "created_at": "2026-08-20T10:00:00+00:00",
        "last_user_message_at": "2026-08-20T10:05:00+00:00",
    }


async def _sweep_with(result):
    cron._pushed_today = set()
    cron._pushed_today_date = ""
    with patch.object(cron.db, "get_abandoned_conversations", new=AsyncMock(return_value=[_conv()])), \
         patch.object(cron, "get_or_create_lead", AsyncMock(return_value={"id": "l1", "created_in_crm": False})), \
         patch("app.api.cron.submit_abandoned_to_crm", new=AsyncMock(return_value=result)), \
         patch.object(cron.db, "update_conversation", new=AsyncMock()), \
         patch.object(cron.db, "mark_lead_created_in_crm", new=AsyncMock(return_value={"id": "l1"})), \
         patch("app.services.closing.claim_closing_sent", new=AsyncMock(return_value=True)), \
         patch.object(cron.db, "add_message", new=AsyncMock()), \
         patch.object(cron, "_persist_refusal", new=AsyncMock(return_value=True)) as refusal:
        out = await cron.run_abandoned_crm()
    cron._pushed_today = set()
    return out, refusal


@pytest.mark.asyncio
@pytest.mark.parametrize("error,level,event", [
    ("gate:email_typo_domain", logging.WARNING, "crm.gate_refused"),
    ("HTTP 502 from CRM", logging.ERROR, "crm.push_failed"),
])
async def test_a_refusal_is_a_warning_and_a_crm_failure_an_error(caplog, error, level, event):
    with caplog.at_level(logging.INFO, logger="app.api.cron"):
        out, refusal = await _sweep_with(MagicMock(success=False, error=error, request_id=None))
    (rec,) = [r for r in caplog.records if "CRM fail" in r.getMessage()]
    assert rec.levelno == level and rec.event == event
    refusal.assert_awaited_once(), "the refusal is still recorded on the lead"
    assert out["results"][0]["status"] == "crm_failed"


# ── the CRM sign-in: counted, not a line ─────────────────────────────────


def _exchange(user=None, verify=None):
    from fastapi import HTTPException

    from app.api import auth_routes

    req = auth_routes.CrmExchangeRequest(token="t")
    patches = [
        patch.object(auth_routes, "verify_crm_token",
                     side_effect=verify or (lambda *a, **k: {"email": "a@buybusinessclass.com"})),
        patch.object(auth_routes.db, "get_user_by_email", new=AsyncMock(return_value=user)),
        patch.object(auth_routes, "_issue_jwt", return_value="jwt"),
    ]
    return auth_routes, req, patches, HTTPException


@pytest.mark.asyncio
async def test_a_crm_sign_in_is_counted_and_written_at_debug_only(caplog):
    auth_routes, req, patches, _ = _exchange(
        user={"id": "u1", "email": "a@buybusinessclass.com", "role": "sales", "is_active": True})
    before = dict(auth_routes.SSO_HEALTH)
    with patches[0], patches[1], patches[2], caplog.at_level(logging.INFO, logger="app.api.auth_routes"):
        res = await auth_routes.crm_sso_exchange(req, None)
    assert res.token == "jwt"
    assert auth_routes.SSO_HEALTH["ok"] == before["ok"] + 1
    assert not [r for r in caplog.records if "CRM SSO exchange login" in r.getMessage()]


@pytest.mark.asyncio
@pytest.mark.parametrize("case,counter,status", [
    ("unknown", "unknown_user", 401),
    ("inactive", "inactive", 403),
    ("token", "token_refused", 401),
])
async def test_every_refusal_is_counted_by_reason(case, counter, status):
    from fastapi import HTTPException

    def bad_token(*a, **k):
        raise HTTPException(401, "SSO token expired — please reload from the CRM")

    user = None if case == "unknown" else {"id": "u1", "email": "a@x", "role": "sales", "is_active": False}
    auth_routes, req, patches, _ = _exchange(user=user, verify=bad_token if case == "token" else None)
    before = auth_routes.SSO_HEALTH[counter]
    with patches[0], patches[1], patches[2], pytest.raises(HTTPException) as err:
        await auth_routes.crm_sso_exchange(req, None)
    assert err.value.status_code == status
    assert auth_routes.SSO_HEALTH[counter] == before + 1
