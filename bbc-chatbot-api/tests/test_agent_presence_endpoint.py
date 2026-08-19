"""The CRM presence gate — state only, and never a lie.

The CRM will not let a sales agent work leads unless that agent is
genuinely present in our chat panel: live visitors are routed only to
agents whose panel is open and who pressed Ready, so an agent sitting in
the CRM with the chat closed leaves visitors waiting for nobody.

THE FINDING THAT SHAPES EVERYTHING HERE: `is_ready` is set ONLY when the
agent presses the button (app/api/agent.py) and NOTHING ever clears it.
Someone who pressed Ready on Monday and went home is still is_ready=true
today — so the flag alone is worthless as presence. Only a fresh pulse
(`users.last_seen_at`, written by the panel's 5s heartbeat) makes it mean
anything.

THE SECOND FINDING: this endpoint and the SSO login exchange verify with
the same secret, so without an audience claim every presence token — one
per lead-open, machine-to-machine, high frequency — is also a full login
credential for that agent's panel account. Hence `purpose`.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api import integration
from app.api.integration import (
    PRESENCE_GATE_HEALTH,
    PRESENCE_RATE_PER_MINUTE,
    AgentPresenceRequest,
    agent_presence,
    check_presence_rate_limit,
    evaluate_presence,
)
from config.settings import settings

SECRET = "shared-with-the-crm"
WINDOW = 90


def _token(email="agent@bbc.com", *, iss="crm", exp_delta=60, secret=SECRET,
           purpose="presence", iat_delta=0, **extra):
    now = datetime.now(timezone.utc)
    payload = {
        "iss": iss, "email": email,
        "iat": int((now + timedelta(seconds=iat_delta)).timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
        **extra,
    }
    if purpose is not None:
        payload["purpose"] = purpose
    return jwt.encode(payload, secret, algorithm="HS256")


def _user(role="sales", ready=True, seen_seconds_ago=5, active=True):
    seen = (
        None if seen_seconds_ago is None
        else (datetime.now(timezone.utc) - timedelta(seconds=seen_seconds_ago)).isoformat()
    )
    return {"id": "u1", "email": "agent@bbc.com", "role": role,
            "is_ready": ready, "last_seen_at": seen, "is_active": active,
            "name": "Maria", "phone": "+12105550100"}


def _empty_ilike():
    """A Supabase client whose case-insensitive fallback finds nobody.

    Every test that expects `unknown_user` must go through the fallback,
    so it has to be stubbed or the suite would reach for the network.
    """
    client = MagicMock()
    client.table.return_value.select.return_value.ilike.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    return client


async def _ask(user, token=None, *, ilike_rows=None):
    client = MagicMock()
    client.table.return_value.select.return_value.ilike.return_value.limit.return_value.execute.return_value = MagicMock(
        data=ilike_rows or []
    )
    with patch.object(settings, "chat_sso_secret", SECRET), \
         patch.object(settings, "crm_presence_window_seconds", WINDOW), \
         patch.object(integration.db, "get_client", return_value=client), \
         patch.object(integration.db, "get_user_by_email",
                      new=AsyncMock(return_value=user)):
        return await agent_presence(
            AgentPresenceRequest(token=token or _token()), None
        )


# ════════════════════════════════════════════════════════════
# The headline: a stale Ready flag is not presence
# ════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_ready_flag_five_minutes_old_is_offline():
    """is_ready=true, role sales, pulse 5 minutes old. Nothing clears the
    flag automatically, so without the window this agent would pass the
    gate from home."""
    out = await _ask(_user(seen_seconds_ago=300))
    assert out["ready"] is False
    assert out["online"] is False
    assert out["reason"] == "offline"
    assert out["last_seen_seconds"] >= 295


@pytest.mark.asyncio
async def test_a_present_ready_agent_passes():
    out = await _ask(_user(seen_seconds_ago=12))
    assert out == {
        "ready": True, "online": True, "exempt": False,
        "reason": "ok", "last_seen_seconds": out["last_seen_seconds"],
    }
    assert out["last_seen_seconds"] <= 15


@pytest.mark.asyncio
async def test_present_with_ready_button_off_is_still_ready():
    """Spec v2.4 A1 (shared-queue wave): is_ready left this gate. The pulse
    already says "I am here", and a button left on overnight lies — the real
    defence against a lying pulse is the response deadline plus the idempotent
    fallback (#211). `not_ready` no longer exists as a reason."""
    out = await _ask(_user(ready=False, seen_seconds_ago=3))
    assert out["online"] is True
    assert out["ready"] is True
    assert out["reason"] == "ok"


@pytest.mark.asyncio
async def test_a_deactivated_agent_is_never_ready():
    """Deactivation does not reach a live session: the panel keeps
    heartbeating on an unexpired JWT, so a disabled account looks
    perfectly present. The login path 403s them (auth_routes.py) — this
    gate must not answer the opposite about the same person."""
    out = await _ask(_user(active=False, seen_seconds_ago=3))
    assert out["online"] is True          # the pulse is real
    assert out["ready"] is False          # but they may not work leads
    assert out["exempt"] is False         # and they are ours to block
    assert out["reason"] == "inactive"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "owner", "dev", "supervisor", "qa"])
async def test_non_operator_roles_are_never_blocked(role):
    out = await _ask(_user(role=role, ready=False, seen_seconds_ago=99999))
    assert out["exempt"] is True
    assert out["reason"] == "wrong_role"


@pytest.mark.asyncio
async def test_a_role_that_does_not_exist_yet_fails_open():
    """Semantics, not a list: a role invented after this code was written
    must never be blocked by code that cannot know about it."""
    out = await _ask(_user(role="senior_sales"))
    assert out["exempt"] is True
    assert out["reason"] == "wrong_role"


@pytest.mark.asyncio
async def test_role_casing_does_not_change_the_answer():
    out = await _ask(_user(role="Sales", seen_seconds_ago=3))
    assert out["exempt"] is False and out["reason"] == "ok"


@pytest.mark.asyncio
async def test_unknown_email_is_200_not_404():
    """One code path for the CRM — and an email we don't know is not an
    agent we may block."""
    out = await _ask(None)
    assert out["exempt"] is True
    assert out["reason"] == "unknown_user"
    assert out["ready"] is False and out["last_seen_seconds"] is None


@pytest.mark.asyncio
async def test_an_agent_who_never_beat_is_offline():
    out = await _ask(_user(seen_seconds_ago=None))
    assert out["online"] is False and out["reason"] == "offline"
    assert out["last_seen_seconds"] is None


def test_the_window_boundary_belongs_to_online():
    assert evaluate_presence(_user(seen_seconds_ago=WINDOW), WINDOW)["online"] is True
    assert evaluate_presence(_user(seen_seconds_ago=WINDOW + 5), WINDOW)["online"] is False


def test_clock_skew_never_reads_negative():
    future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    out = evaluate_presence({**_user(), "last_seen_at": future}, WINDOW)
    assert out["last_seen_seconds"] == 0 and out["online"] is True


def test_naive_timestamps_do_not_raise():
    naive = (datetime.now(timezone.utc) - timedelta(seconds=20)).replace(tzinfo=None)
    out = evaluate_presence({**_user(), "last_seen_at": naive.isoformat()}, WINDOW)
    assert out["online"] is True


def test_a_row_without_is_active_is_treated_as_active():
    """Older rows predate the column; absence must not read as disabled,
    or every one of them would be blocked."""
    row = _user(seen_seconds_ago=3)
    row.pop("is_active")
    assert evaluate_presence(row, WINDOW)["reason"] == "ok"


# ════════════════════════════════════════════════════════════
# A presence token is NOT a login credential
# ════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_login_token_cannot_check_presence():
    """Same secret, different job. Without this the CRM could replay the
    token from a browser URL against this endpoint."""
    with patch.object(settings, "chat_sso_secret", SECRET):
        with pytest.raises(HTTPException) as exc:
            await agent_presence(
                AgentPresenceRequest(token=_token(purpose="login")), None
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_a_token_with_no_purpose_cannot_check_presence():
    """The endpoint is new — there is no legacy shape to protect, so it
    demands the claim rather than inferring it."""
    with patch.object(settings, "chat_sso_secret", SECRET):
        with pytest.raises(HTTPException) as exc:
            await agent_presence(
                AgentPresenceRequest(token=_token(purpose=None)), None
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_aud_is_accepted_as_an_alias_for_purpose():
    tok = jwt.encode(
        {"iss": "crm", "email": "agent@bbc.com", "aud": "presence",
         "iat": int(datetime.now(timezone.utc).timestamp()),
         "exp": int((datetime.now(timezone.utc) + timedelta(seconds=60)).timestamp())},
        SECRET, algorithm="HS256",
    )
    out = await _ask(_user(seen_seconds_ago=3), token=tok)
    assert out["reason"] == "ok"


@pytest.mark.asyncio
async def test_a_presence_token_cannot_open_a_session():
    """The other direction, which is the one that costs an account."""
    from app.api import auth_routes

    with patch.object(settings, "chat_sso_secret", SECRET):
        with pytest.raises(HTTPException) as exc:
            await auth_routes.crm_sso_exchange(
                auth_routes.CrmExchangeRequest(token=_token(purpose="presence")), None
            )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_an_old_but_unexpired_token_is_refused():
    """Minted per check: a fifteen-minute window would leave a replayable
    credential lying in the CRM's logs for fifteen minutes."""
    stale = _token(iat_delta=-600, exp_delta=600)
    with patch.object(settings, "chat_sso_secret", SECRET):
        with pytest.raises(HTTPException) as exc:
            await agent_presence(AgentPresenceRequest(token=stale), None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_a_token_from_a_fast_clock_is_refused_exactly_as_login_is():
    """PyJWT rejects an `iat` in the future with no leeway, and the login
    exchange has always answered 401 to it. Verification is EXTRACTED,
    not redesigned, so the gate answers the same — and because the CRM
    fails open on 401, a drifting clock disables the gate rather than
    locking agents out. Their side must stay NTP-synced (documented)."""
    with patch.object(settings, "chat_sso_secret", SECRET):
        with pytest.raises(HTTPException) as exc:
            await agent_presence(
                AgentPresenceRequest(token=_token(iat_delta=20)), None
            )
    assert exc.value.status_code == 401


# ════════════════════════════════════════════════════════════
# Identity comes from the SIGNED token — never from the URL
# ════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_the_email_comes_from_the_token_payload():
    seen: dict = {}

    async def _lookup(email):
        seen["email"] = email
        return _user()

    with patch.object(settings, "chat_sso_secret", SECRET), \
         patch.object(integration.db, "get_user_by_email", new=_lookup):
        await agent_presence(
            AgentPresenceRequest(token=_token("real@bbc.com")), None
        )
    # An unrelated ?email= on the URL cannot reach this function at all:
    # the signature takes no such parameter, by design.
    assert seen["email"] == "real@bbc.com"
    import inspect

    params = set(inspect.signature(agent_presence).parameters)
    assert params == {"req", "_rate"}


@pytest.mark.asyncio
async def test_sub_is_the_fallback_when_it_is_an_email():
    seen: dict = {}

    async def _lookup(email):
        seen["email"] = email
        return _user()

    tok = _token(email=None, sub="fallback@bbc.com")
    with patch.object(settings, "chat_sso_secret", SECRET), \
         patch.object(integration.db, "get_user_by_email", new=_lookup):
        await agent_presence(AgentPresenceRequest(token=tok), None)
    assert seen["email"] == "fallback@bbc.com"


@pytest.mark.asyncio
async def test_a_sub_that_is_an_internal_id_is_never_looked_up():
    """The CRM's documented `sub` is their user id ("123"). Looking that
    up by email resolves every agent to unknown_user — a gate that is on,
    that answers, and that never blocks anybody."""
    lookup = AsyncMock(return_value=_user())
    with patch.object(settings, "chat_sso_secret", SECRET), \
         patch.object(integration.db, "get_user_by_email", new=lookup):
        out = await agent_presence(
            AgentPresenceRequest(token=_token(email=None, sub="123")), None
        )
    lookup.assert_not_awaited()
    assert out["reason"] == "unknown_user" and out["exempt"] is True


@pytest.mark.asyncio
async def test_a_casing_mismatch_still_finds_the_agent():
    """We lower-case what the CRM sends; the users table is not
    guaranteed to store it that way."""
    out = await _ask(None, ilike_rows=[_user(seen_seconds_ago=3)])
    assert out["reason"] == "ok" and out["exempt"] is False


# ════════════════════════════════════════════════════════════
# Token failures
# ════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.parametrize("token,status", [
    (_token(exp_delta=-10), 401),                 # expired
    (_token(iss="not-crm"), 401),                 # wrong issuer
    (_token(secret="someone-elses-secret"), 401), # bad signature
    ("not-a-jwt-at-all-just-a-long-string", 401), # malformed
])
async def test_bad_tokens_are_401(token, status):
    with patch.object(settings, "chat_sso_secret", SECRET), \
         patch.object(integration.db, "get_user_by_email", new=AsyncMock()) as lookup:
        with pytest.raises(HTTPException) as exc:
            await agent_presence(AgentPresenceRequest(token=token), None)
    assert exc.value.status_code == status
    lookup.assert_not_awaited()      # never touches the DB on a bad token


@pytest.mark.asyncio
async def test_missing_secret_is_503():
    with patch.object(settings, "chat_sso_secret", ""):
        with pytest.raises(HTTPException) as exc:
            await agent_presence(AgentPresenceRequest(token=_token()), None)
    assert exc.value.status_code == 503


# ════════════════════════════════════════════════════════════
# The shape of the answer — state, not people
# ════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_exactly_five_fields_and_no_pii():
    out = await _ask(_user())
    assert set(out) == {"ready", "online", "exempt", "reason", "last_seen_seconds"}
    blob = str(out)
    for leaked in ("Maria", "agent@bbc.com", "+12105550100", "u1"):
        assert leaked not in blob


@pytest.mark.asyncio
async def test_the_endpoint_writes_nothing():
    import inspect

    src = inspect.getsource(integration)
    for writer in ("update_user(", "update_conversation(", ".insert(", ".update("):
        assert writer not in src


@pytest.mark.asyncio
async def test_a_lookup_failure_does_not_block_anyone():
    with patch.object(settings, "chat_sso_secret", SECRET), \
         patch.object(integration.db, "get_user_by_email",
                      new=AsyncMock(side_effect=RuntimeError("db down"))):
        out = await agent_presence(AgentPresenceRequest(token=_token()), None)
    assert out["exempt"] is True and out["reason"] == "unknown_user"


# ════════════════════════════════════════════════════════════
# Rate limit — sized for a backend caller, not for a browser
# ════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_the_gate_survives_a_busy_crm_day():
    """The shared widget limiter allows 300 requests per IP per DAY. The
    CRM's backend is ONE IP asking once per lead-open, so that budget
    dies before lunch and every later check 429s — which the contract
    turns into "do not block". A dead gate that looks alive is worse than
    no gate."""
    integration._presence_hits.clear()
    for _ in range(300):
        await check_presence_rate_limit(None)
    assert len(integration._presence_hits["unknown"]) == 300


@pytest.mark.asyncio
async def test_the_limiter_still_has_a_ceiling():
    """Bounded: a leaked token cannot be used to hammer the DB."""
    integration._presence_hits.clear()
    for _ in range(PRESENCE_RATE_PER_MINUTE):
        await check_presence_rate_limit(None)
    with pytest.raises(HTTPException) as exc:
        await check_presence_rate_limit(None)
    assert exc.value.status_code == 429
    integration._presence_hits.clear()


# ════════════════════════════════════════════════════════════
# Telemetry — aggregate only
# ════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_health_counts_checks_and_blocks_but_names_nobody():
    PRESENCE_GATE_HEALTH.update({"checks": 0, "blocked": 0, "errors": 0})

    await _ask(_user(seen_seconds_ago=300))        # blocked
    await _ask(_user(seen_seconds_ago=5))          # ok
    await _ask(_user(role="admin"))                # exempt → not blocked

    assert PRESENCE_GATE_HEALTH == {"checks": 3, "blocked": 1, "errors": 0}

    import inspect

    src = inspect.getsource(integration)
    assert "email" not in src.split("PRESENCE_GATE_HEALTH")[-1]


@pytest.mark.asyncio
async def test_a_failing_gate_is_visible_without_reading_logs():
    """401/503 make the CRM fail open — the gate is OFF while looking
    perfectly alive from the outside. Ops must see that on /health."""
    PRESENCE_GATE_HEALTH.update({"checks": 0, "blocked": 0, "errors": 0})

    with patch.object(settings, "chat_sso_secret", SECRET):
        with pytest.raises(HTTPException):
            await agent_presence(AgentPresenceRequest(token=_token(purpose="login")), None)
    with patch.object(settings, "chat_sso_secret", ""):
        with pytest.raises(HTTPException):
            await agent_presence(AgentPresenceRequest(token=_token()), None)

    assert PRESENCE_GATE_HEALTH["errors"] == 2
    assert PRESENCE_GATE_HEALTH["checks"] == 0     # a refusal is not an answer


def test_health_exposes_the_gate_in_both_payloads():
    path = os.path.join(os.path.dirname(__file__), "..", "app", "api", "health.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert src.count('"presence_gate"') == 2


# ════════════════════════════════════════════════════════════
# REGRESSION: /sso/crm-exchange behaves exactly as before
# ════════════════════════════════════════════════════════════

class TestSsoExchangeUnchanged:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("token,status", [
        (_token(exp_delta=-10, purpose=None), 401),
        (_token(iss="not-crm", purpose=None), 401),
        ("garbage-token-value-that-is-long", 401),
    ])
    async def test_bad_tokens_still_401(self, token, status):
        from app.api import auth_routes

        with patch.object(settings, "chat_sso_secret", SECRET):
            with pytest.raises(HTTPException) as exc:
                await auth_routes.crm_sso_exchange(
                    auth_routes.CrmExchangeRequest(token=token), None
                )
        assert exc.value.status_code == status

    @pytest.mark.asyncio
    async def test_missing_secret_still_503(self):
        from app.api import auth_routes

        with patch.object(settings, "chat_sso_secret", ""):
            with pytest.raises(HTTPException) as exc:
                await auth_routes.crm_sso_exchange(
                    auth_routes.CrmExchangeRequest(token=_token(purpose=None)), None
                )
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    @pytest.mark.parametrize("purpose", [None, "login"])
    async def test_the_happy_path_still_issues_our_own_jwt(self, purpose):
        """Absent is the CRM's shape TODAY and must keep working; "login"
        is what they will send once they stamp their tokens."""
        from app.api import auth_routes

        with patch.object(settings, "chat_sso_secret", SECRET), \
             patch.object(settings, "jwt_secret", "our-own-secret"), \
             patch.object(auth_routes.db, "get_user_by_email", new=AsyncMock(
                 return_value={"id": "u1", "email": "agent@bbc.com", "name": "Maria",
                               "role": "sales", "tunnel_scope": "sales",
                               "is_active": True})):
            out = await auth_routes.crm_sso_exchange(
                auth_routes.CrmExchangeRequest(token=_token(purpose=purpose)), None
            )
        claims = jwt.decode(out.token, "our-own-secret", algorithms=["HS256"])
        assert claims["email"] == "agent@bbc.com"
        assert claims["role"] == "sales"       # from OUR db, never the CRM token

    @pytest.mark.asyncio
    async def test_a_token_without_email_still_400s(self):
        from app.api import auth_routes

        tok = jwt.encode(
            {"iss": "crm", "iat": int(datetime.now(timezone.utc).timestamp()),
             "exp": int((datetime.now(timezone.utc) + timedelta(seconds=60)).timestamp())},
            SECRET, algorithm="HS256",
        )
        with patch.object(settings, "chat_sso_secret", SECRET):
            with pytest.raises(HTTPException) as exc:
                await auth_routes.crm_sso_exchange(
                    auth_routes.CrmExchangeRequest(token=tok), None
                )
        assert exc.value.status_code == 400

    def test_the_verification_lives_in_exactly_one_place(self):
        import inspect

        from app.api import auth_routes

        src = inspect.getsource(auth_routes.crm_sso_exchange)
        assert "verify_crm_token(req.token, expected_purpose=PURPOSE_LOGIN)" in src
        assert "jwt.decode" not in src          # the inline copy is gone
