"""The request middleware: Railway's request id on every line, and an access
line only for a request that failed on our side or was slow.

9 Nov: 89% of our log was successful requests, written twice (bbc.access and
uvicorn.access), while Railway's HTTP log already held each one with the IP
and browser. Railway then dropped 75,211 lines in a week, in the minutes of
the incidents.
"""

import asyncio
import logging
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.log_setup import RequestIdFilter, request_id_var
from app.security.request_logger import RequestLoggerMiddleware


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestLoggerMiddleware)

    @app.get("/ok")
    async def ok():
        return {"rid": request_id_var.get()}

    @app.get("/broken")
    async def broken():
        return JSONResponse({"detail": "upstream"}, status_code=502)

    @app.get("/missing")
    async def missing():
        return JSONResponse({"detail": "nope"}, status_code=404)

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(0.05)
        return {}

    return TestClient(app)


def _access(caplog):
    return [r for r in caplog.records if r.name == "bbc.access"]


def test_a_successful_request_writes_no_line(caplog):
    with caplog.at_level(logging.DEBUG, logger="bbc.access"):
        for _ in range(20):
            assert _client().get("/ok").status_code == 200
    assert [r for r in _access(caplog) if r.levelno >= logging.INFO] == []


def test_a_4xx_is_not_ours_to_log(caplog):
    with caplog.at_level(logging.INFO, logger="bbc.access"):
        assert _client().get("/missing").status_code == 404
    assert _access(caplog) == []


class _Collect(logging.Handler):
    """A handler with the production request-id filter on it."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []
        self.addFilter(RequestIdFilter())

    def emit(self, record):
        self.records.append(record)


def test_a_5xx_is_a_warning_with_its_numbers_and_its_request_id():
    collect = _Collect()
    access = logging.getLogger("bbc.access")
    access.addHandler(collect)
    try:
        res = _client().get("/broken", headers={"X-Railway-Request-Id": "Rw-req-0001abcd"})
    finally:
        access.removeHandler(collect)
    assert res.status_code == 502
    (rec,) = collect.records
    assert rec.levelno == logging.WARNING
    assert rec.event == "http.5xx" and rec.status == 502 and rec.path == "/broken"
    assert isinstance(rec.duration_ms, int)
    assert rec.rid == "Rw-req-0001abcd", "@requestId in Railway's HTTP log finds this request"


def test_a_slow_request_is_a_warning(monkeypatch, caplog):
    monkeypatch.setenv("LOG_SLOW_REQUEST_MS", "20")
    with caplog.at_level(logging.INFO, logger="bbc.access"):
        assert _client().get("/slow").status_code == 200
    (rec,) = _access(caplog)
    assert rec.levelno == logging.WARNING and rec.event == "http.slow"
    assert rec.duration_ms >= 20


def test_railways_request_id_is_the_one_we_use_and_return():
    res = _client().get("/ok", headers={"X-Railway-Request-Id": "Rw-req-0001abcd"})
    assert res.json()["rid"] == "Rw-req-0001abcd", "the endpoint's lines carry it"
    assert res.headers["X-Request-ID"] == "Rw-req-0001abcd"


@pytest.mark.parametrize("sent", [None, "short", "has space inside!", "x" * 80])
def test_without_a_usable_railway_id_we_make_our_own(sent):
    headers = {"X-Railway-Request-Id": sent} if sent else {}
    res = _client().get("/ok", headers=headers)
    rid = res.headers["X-Request-ID"]
    assert rid != sent and len(rid) == 12 and res.json()["rid"] == rid


def test_the_id_does_not_leak_out_of_the_request():
    _client().get("/ok", headers={"X-Railway-Request-Id": "Rw-req-0001abcd"})
    assert request_id_var.get() is None


@pytest.mark.asyncio
async def test_a_db_thread_sees_the_request_id():
    """_run_sync runs fn in a copy of the caller's context: a line written
    inside a DB call carries the request it served."""
    from app.db import supabase as sb

    tok = request_id_var.set("rid-in-thread")
    try:
        seen = await sb._run_sync(lambda: request_id_var.get())
    finally:
        request_id_var.reset(tok)
    assert seen == "rid-in-thread"
