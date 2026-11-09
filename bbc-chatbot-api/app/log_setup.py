"""Logging: one line per event, with the level Railway can filter on.

Railway, 2–9 Nov 2026 (claude/DEZBATERI_loguri_semnal_vs_zgomot_2026-11-09.md):
- every line of ours went to stderr, which Railway tags "error" — INFO
  included — so its error filter showed everything;
- a traceback reached Railway as ~45 separate entries, interleaved with other
  requests' lines, and a search for the error found only its first line.

On Railway (or with LOG_FORMAT=json) each record is ONE JSON line on stdout:
`level` (debug/info/warn/error, what Railway reads), `message`, `logger`, an
`event` code, the request id `rid` when there is one, and the traceback inside
`exc`. Keys given through `extra=` become attributes Railway filters on
(`@event:db.retry`, `@duration_ms:>2000`) — except the ones that would carry a
person (`_FORBIDDEN_KEYS`): rule 7 is a rule for attributes too.

Off Railway and in tests the format is the old text line on stderr, exactly as
before. LOG_FORMAT=text on Railway is also the rollback.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from typing import Optional

# The request this code is running for. Set by the request logger (Railway's
# X-Railway-Request-Id when it sent one) and copied into the DB threads by
# _run_sync, so a line written anywhere inside a request carries it.
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

TEXT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
TEXT_DATEFMT = "%H:%M:%S"

# What Railway understands as a level.
_LEVELS = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}

# Never written as attributes, whoever passes them in `extra=`.
_FORBIDDEN_KEYS = frozenset({
    "email", "phone", "name", "first_name", "last_name", "full_name",
    "visitor_name", "content", "text", "body", "message_text", "transcript",
    "token", "password", "authorization", "cookie", "jwt", "secret",
})

# Attributes every LogRecord has, plus the ones this module writes itself and
# uvicorn's colour copy of its own message.
_RECORD_KEYS = frozenset(vars(logging.LogRecord("x", 0, "", 0, "", (), None))) | {
    "message", "asctime", "rid", "event", "color_message", "taskName",
}

# A message longer than this is cut: one line must never be a page (the
# scheduler used to print 200 conversation ids every 5 minutes, ~15 KB).
MAX_MESSAGE_CHARS = 4000


class RequestIdFilter(logging.Filter):
    """Stamps the current request id on the record (if it has none)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "rid", None) is None:
            record.rid = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per record, on one line."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if len(message) > MAX_MESSAGE_CHARS:
            cut = len(message) - MAX_MESSAGE_CHARS
            message = f"{message[:MAX_MESSAGE_CHARS]}… (+{cut} chars)"
        out: dict = {
            "level": _LEVELS.get(record.levelno, "info"),
            "message": message,
            "logger": record.name,
            # An explicit code when the call site gave one, else where it came
            # from — stable across deploys, unlike the text.
            "event": getattr(record, "event", None) or f"{record.name}.{record.funcName}",
        }
        rid = getattr(record, "rid", None)
        if rid:
            out["rid"] = rid
        for key, value in record.__dict__.items():
            if key in _RECORD_KEYS or key.startswith("_") or key.lower() in _FORBIDDEN_KEYS:
                continue
            out[key] = value if isinstance(value, (str, int, float, bool)) or value is None else str(value)
        if record.exc_info:
            exc_type = record.exc_info[0]
            out["exc_type"] = exc_type.__name__ if exc_type else None
            out["exc"] = self.formatException(record.exc_info)
        elif record.exc_text:
            out["exc"] = record.exc_text
        if record.stack_info:
            out["stack"] = self.formatStack(record.stack_info)
        return json.dumps(out, ensure_ascii=False, default=str)


def log_format() -> str:
    """'json' on Railway, 'text' anywhere else; LOG_FORMAT decides when set."""
    chosen = (os.environ.get("LOG_FORMAT") or "").strip().lower()
    if chosen in ("json", "text"):
        return chosen
    on_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT_NAME") or os.environ.get("RAILWAY_REPLICA_ID"))
    return "json" if on_railway else "text"


def _build_handler(fmt: str) -> logging.Handler:
    if fmt == "json":
        handler: logging.Handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(TEXT_FORMAT, datefmt=TEXT_DATEFMT))
    handler.addFilter(RequestIdFilter())
    handler._bbc_handler = True  # type: ignore[attr-defined]
    return handler


def configure_logging(debug: bool = False) -> logging.Handler:
    """Install the one handler on the root logger. Safe to call twice: the
    previous handler of ours is replaced, nobody else's is touched."""
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_bbc_handler", False):
            root.removeHandler(existing)
    handler = _build_handler(log_format())
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # httpx logs the full URL of every Supabase request at INFO — PostgREST puts
    # filters and looked-up values in the query string, so that is PII at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # uvicorn configures its own loggers before it imports this app. Its error
    # logger joins ours (same format, same level, traceback in one entry); its
    # access log is off: Railway's HTTP log already holds every request, with
    # the client's IP and browser, which uvicorn's line never had.
    for name in ("uvicorn", "uvicorn.error"):
        uv = logging.getLogger(name)
        uv.handlers = []
        uv.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
    access.disabled = True
    return handler
