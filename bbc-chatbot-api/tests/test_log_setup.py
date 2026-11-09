"""Logging format (app/log_setup.py).

Railway, 2–9 Nov: every line of ours was tagged "error" (stderr), and a
traceback arrived as ~45 separate entries. These tests pin one JSON line per
event with the real level, the traceback inside it, the request id, and no
person's data in the attributes; and the old text format off Railway.
"""

import io
import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import log_setup
from app.log_setup import JsonFormatter, RequestIdFilter, request_id_var


def _record(msg="hello %s", args=("world",), level=logging.INFO, name="app.test", exc_info=None, **extra):
    rec = logging.LogRecord(name, level, "/app/app/x.py", 12, msg, args, exc_info, func="do_thing")
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def _json(rec):
    line = JsonFormatter().format(rec)
    assert "\n" not in line, "one event = one line"
    return json.loads(line)


@pytest.mark.parametrize(
    "level,expected",
    [(logging.DEBUG, "debug"), (logging.INFO, "info"), (logging.WARNING, "warn"),
     (logging.ERROR, "error"), (logging.CRITICAL, "error")],
)
def test_the_level_is_the_real_one(level, expected):
    assert _json(_record(level=level))["level"] == expected


def test_message_logger_and_an_automatic_event_code():
    out = _json(_record())
    assert out["message"] == "hello world"
    assert out["logger"] == "app.test"
    assert out["event"] == "app.test.do_thing"


def test_an_explicit_event_code_wins():
    assert _json(_record(event="db.retry"))["event"] == "db.retry"


def test_a_traceback_stays_inside_its_own_entry():
    try:
        raise ValueError("boom")
    except ValueError:
        rec = _record(level=logging.ERROR, exc_info=sys.exc_info())
    out = _json(rec)
    assert out["exc_type"] == "ValueError"
    assert "Traceback (most recent call last)" in out["exc"]
    assert "ValueError: boom" in out["exc"]


def test_extra_keys_become_attributes_but_never_a_persons_data():
    out = _json(_record(duration_ms=2300, status=500, email="a@b.c", phone="+1",
                        name="Ann", token="t", content="hi, I'm Ann"))
    assert out["duration_ms"] == 2300 and out["status"] == 500
    for key in ("email", "phone", "name", "token", "content"):
        assert key not in out


def test_the_request_id_is_stamped_when_there_is_one():
    rec = _record()
    tok = request_id_var.set("req-123456")
    try:
        RequestIdFilter().filter(rec)
    finally:
        request_id_var.reset(tok)
    assert _json(rec)["rid"] == "req-123456"

    outside = _record()
    RequestIdFilter().filter(outside)
    assert "rid" not in _json(outside)


def test_a_page_long_message_is_cut():
    out = _json(_record(msg="x" * (log_setup.MAX_MESSAGE_CHARS + 50), args=()))
    assert out["message"].endswith("… (+50 chars)")


def test_json_on_railway_text_elsewhere_and_log_format_decides(monkeypatch):
    for var in ("LOG_FORMAT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_REPLICA_ID"):
        monkeypatch.delenv(var, raising=False)
    assert log_setup.log_format() == "text"
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    assert log_setup.log_format() == "json"
    monkeypatch.setenv("LOG_FORMAT", "text")
    assert log_setup.log_format() == "text", "the rollback is one variable"


def test_configure_installs_one_handler_and_silences_uvicorns_access_log(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        first = log_setup.configure_logging()
        second = log_setup.configure_logging()
        ours = [h for h in root.handlers if getattr(h, "_bbc_handler", False)]
        assert ours == [second] and first not in root.handlers, "calling twice must not double every line"
        assert isinstance(second.formatter, JsonFormatter)
        assert second.stream is sys.stdout
        assert logging.getLogger("uvicorn.access").disabled is True
        for name in ("uvicorn", "uvicorn.error"):
            assert logging.getLogger(name).handlers == [] and logging.getLogger(name).propagate
        assert logging.getLogger("httpx").level == logging.WARNING
    finally:
        for h in list(root.handlers):
            if getattr(h, "_bbc_handler", False):
                root.removeHandler(h)
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        logging.getLogger("uvicorn.access").disabled = False


def test_text_mode_keeps_the_old_line(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "text")
    handler = log_setup._build_handler(log_setup.log_format())
    buf = io.StringIO()
    handler.stream = buf
    handler.handle(_record(level=logging.WARNING))
    line = buf.getvalue()
    assert " | WARNING | app.test | hello world" in line
