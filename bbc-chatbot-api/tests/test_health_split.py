"""/health says only that the process is alive; the counters need a key.

Before, the public /health returned every internal counter: queue depth,
DB latency, which subsystems were degraded — a map for whoever asked.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt as _jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api.health import router
from config.settings import settings

_SECRET = "jwt-health-test"
_CRON = "cron-health-test"


def _client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _token(role):
    return _jwt.encode(
        {"sub": "u1", "role": role, "tunnel_scope": "all",
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        _SECRET, algorithm="HS256",
    )


@pytest.fixture(autouse=True)
def _secrets():
    with patch.object(settings, "jwt_secret", _SECRET), \
         patch.object(settings, "cron_secret", _CRON), \
         patch.object(settings, "debug", False), \
         patch("app.api.health.get_scheduler_health", return_value={}):
        yield


def test_public_health_is_status_only():
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_detail_without_credentials_is_401():
    assert _client().get("/health/detail").status_code == 401


def test_detail_with_cron_secret():
    r = _client().get("/health/detail", headers={"Authorization": f"Bearer {_CRON}"})
    assert r.status_code == 200
    body = r.json()
    assert "lead_writes" in body and "flags" in body and "client_ip" in body


@pytest.mark.parametrize("role,code", [("owner", 200), ("admin", 200), ("dev", 200),
                                        ("sales", 403), ("qa", 403)])
def test_detail_by_role(role, code):
    r = _client().get("/health/detail", headers={"Authorization": f"Bearer {_token(role)}"})
    assert r.status_code == code


def test_wrong_cron_secret_is_401():
    r = _client().get("/health/detail", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_ip_echo_needs_the_cron_secret():
    c = _client()
    assert c.get("/health/ip-echo").status_code == 401
    assert c.get("/health/ip-echo", headers={"Authorization": f"Bearer {_token('owner')}"}).status_code == 401
    r = c.get("/health/ip-echo", headers={"Authorization": f"Bearer {_CRON}", "X-Forwarded-For": "198.51.100.4"})
    assert r.status_code == 200 and r.json() == {"client_ip": "198.51.100.4"}


def test_no_cron_secret_configured_never_matches_an_empty_bearer():
    with patch.object(settings, "cron_secret", ""):
        assert _client().get("/health/ip-echo", headers={"Authorization": "Bearer "}).status_code == 401
