"""PostgREST goes over HTTP/1.1.

postgrest-py 0.16.11 opened its session with http2=True: every thread of the
DB executor shared one HTTP/2 connection, and httpcore 1.0.9's sync HTTP/2
neither locks the stream id against other threads nor saves the requests the
server already processed when it ends the connection (GOAWAY). A write that
landed then read as an error — chat #3245 (4 Nov): the operator who won the
Take was told a colleague had it. These tests pin the protocol, and that the
switch kept everything else the session sends.
"""

import base64
import json
import os
import sys

import httpx
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

from app.db import supabase as sb
from config.settings import settings


@pytest.fixture
def fresh_client():
    """A real client, built the way production builds it (no network)."""
    saved = sb._client
    sb._client = None
    try:
        yield sb.get_client()
    finally:
        built = sb._client
        sb._client = saved
        if built is not None and built._postgrest is not None:
            built._postgrest.session.close()


def _pool(session: httpx.Client):
    # httpx keeps the protocol choice on its transport's httpcore pool.
    return session._transport._pool


def test_postgrest_session_is_http1_only(fresh_client):
    pool = _pool(fresh_client.postgrest.session)
    assert pool._http2 is False
    assert pool._http1 is True


def test_the_switch_kept_headers_url_timeout_and_redirects(fresh_client):
    s = fresh_client.postgrest.session
    assert str(s.base_url) == f"{settings.supabase_url}/rest/v1/"
    assert s.headers["apikey"] == settings.supabase_key
    assert s.headers["Authorization"] == f"Bearer {settings.supabase_key}"
    assert s.headers["Accept-Profile"] == "public"
    assert s.headers["Content-Profile"] == "public"
    assert s.headers.get("X-Client-Info", "").startswith("supabase-py/")
    assert s.timeout == httpx.Timeout(10), "PR-9: a wedged query must not hold a turn"
    assert s.follow_redirects is True


def test_one_kept_connection_per_db_worker(fresh_client):
    """HTTP/1.1 carries one request at a time: a pool smaller than the
    executor would close and reopen (a new TLS handshake) under load."""
    assert _pool(fresh_client.postgrest.session)._max_keepalive_connections >= settings.db_executor_workers


def test_table_and_rpc_calls_ride_that_session(fresh_client):
    s = fresh_client.postgrest.session
    assert fresh_client.table("conversations").session is s
    assert fresh_client.rpc("patch_conv_presence", {}).session is s


def test_a_rebuilt_postgrest_client_is_http1_too(fresh_client):
    """supabase-py drops its PostgREST client on an auth event and builds a
    new one on next use: the new one must not fall back to HTTP/2."""
    first = fresh_client.postgrest
    fresh_client._postgrest = None
    second = fresh_client.postgrest
    try:
        assert second is not first
        assert _pool(second.session)._http2 is False
    finally:
        first.session.close()
