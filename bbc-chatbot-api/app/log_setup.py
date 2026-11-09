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

Repetitions (9 Nov: one refused lead was the same ERROR every 5 minutes for
days; a transport storm wrote hundreds of identical lines a second and Railway
dropped 75,211 lines in a week, past its cap of 500 a second): the first line
of a kind is written in full; the same line again is held back and counted,
and comes out as one line saying how many and how many distinct ids, after 5
minutes, then 10, 20, 40, at most every 60 while it keeps coming. A safety
valve holds the rest under 100 lines a second. ERROR and app.security.* are
never held back by the valve, and app.security.* is never grouped either.
Every WARNING and ERROR, held back or not, is counted per day for
/health/detail → logs. LOG_GROUPING=false (a runtime flag) turns grouping off.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Callable, Optional

from app.flags import flag

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

# Never written as attributes, whoever passes them in `extra=`. Generic words
# match only exactly: `content` or `text` alone is a message, `label_text` is
# not. (`name` is not here: it is the logger's own attribute, written as
# "logger", and logging refuses it in extra=.)
_FORBIDDEN_KEYS = frozenset({
    "email", "phone", "first_name", "last_name", "full_name", "visitor_name",
    "customer_name", "client_name", "contact_name", "username", "user_name",
    "content", "text", "body", "message_text", "transcript",
    "token", "password", "authorization", "cookie", "jwt", "secret",
})

# A contact or a credential, wherever the word sits in the key:
# visitor_email, phone_number, access_token, refreshToken, api_key, passwd.
# Whole words of the key only, so input_tokens (a count) still passes.
_FORBIDDEN_WORDS = frozenset({
    "email", "phone", "token", "password", "passwd", "secret", "authorization",
    "cookie", "jwt", "apikey", "transcript",
})
_KEY_WORDS = re.compile(r"[_\-.]|(?<=[a-z0-9])(?=[A-Z])")


def _forbidden(key: str) -> bool:
    """True for a key that names a person's data or a credential."""
    if key.lower() in _FORBIDDEN_KEYS:
        return True
    words = [w.lower() for w in _KEY_WORDS.split(key) if w]
    pairs = (a + b for a, b in zip(words, words[1:]))  # api_key, pass_word
    return any(w in _FORBIDDEN_WORDS for w in words) or any(p in _FORBIDDEN_WORDS for p in pairs)

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
            if key in _RECORD_KEYS or key.startswith("_") or _forbidden(key):
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


# ── Repetitions, the safety valve, and the day's count ───────────────────

GROUP_FIRST_WINDOW_S = 300.0   # the first summary comes after 5 minutes
GROUP_MAX_WINDOW_S = 3600.0    # and never less often than once an hour
GROUP_RESET_AFTER_S = 3600.0   # an hour of silence: the next one is new again
_MAX_GROUPS = 2000
_MAX_IDS = 500
# Security lines are evidence: never grouped, never held back by the valve.
_EXEMPT_PREFIXES = ("app.security.",)

_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX_RE = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_SQUOTE_RE = re.compile(r"'[^']{0,200}'")
_DQUOTE_RE = re.compile(r'"[^"]{0,200}"')
_NUM_RE = re.compile(r"\d+")


def _shape(message: str) -> str:
    """The message with what varies taken out: ids, numbers, quoted values."""
    m = _UUID_RE.sub("<id>", message)
    m = _HEX_RE.sub("<hex>", m)
    m = _SQUOTE_RE.sub("'…'", m)
    m = _DQUOTE_RE.sub('"…"', m)
    m = _NUM_RE.sub("#", m)
    return m[:160]


def _event_of(record: logging.LogRecord) -> str:
    return getattr(record, "event", None) or f"{record.name}.{record.funcName}"


def _exempt(record: logging.LogRecord) -> bool:
    return record.name.startswith(_EXEMPT_PREFIXES)


class EventRegistry:
    """Every WARNING and ERROR of the day (UTC), held back or not, by kind."""

    MAX_KINDS = 500

    def __init__(self, today: Callable[[], str] = lambda: datetime.now(timezone.utc).date().isoformat()):
        self._today = today
        self._lock = threading.Lock()
        self._day: Optional[str] = None
        self._kinds: dict[str, dict] = {}
        self.warnings = 0
        self.errors = 0

    def note(self, record: logging.LogRecord, message: str) -> None:
        if record.levelno < logging.WARNING:
            return
        day = self._today()
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        event = _event_of(record)
        key = f"{event} · {_shape(message)[:100]}"
        with self._lock:
            if day != self._day:
                self._day, self._kinds, self.warnings, self.errors = day, {}, 0, 0
            if record.levelno >= logging.ERROR:
                self.errors += 1
            else:
                self.warnings += 1
            kind = self._kinds.get(key)
            if kind is None:
                if len(self._kinds) >= self.MAX_KINDS:
                    return  # still in the totals above
                kind = self._kinds[key] = {
                    "event": event, "level": record.levelname, "count": 0,
                    "first": stamp, "last": stamp, "sample": message[:300],
                }
            kind["count"] += 1
            kind["last"] = stamp

    def snapshot(self, top: int = 20) -> dict:
        with self._lock:
            kinds = sorted(self._kinds.values(), key=lambda k: -k["count"])[:top]
            return {
                "day": self._day, "warnings": self.warnings, "errors": self.errors,
                "kinds": len(self._kinds), "top": [dict(k) for k in kinds],
            }


class _Group:
    __slots__ = ("logger", "level", "event", "last_at", "last_emit", "interval",
                 "held", "ids", "last_message")

    def __init__(self, record: logging.LogRecord, message: str, now: float):
        self.logger = record.name
        self.level = record.levelno
        self.event = _event_of(record)
        self.last_at = now
        self.last_emit = now
        self.interval = GROUP_FIRST_WINDOW_S
        self.held = 0
        self.ids: set = set()
        self.last_message = message


class GroupingFilter(logging.Filter):
    """Writes the first line of a kind, holds back and counts the same line
    again, and says how many in one line (here or in flush())."""

    def __init__(self, registry: Optional[EventRegistry] = None, clock: Callable[[], float] = time.monotonic):
        super().__init__()
        self.registry = registry
        self._clock = clock
        self._lock = threading.Lock()
        self._groups: dict[tuple, _Group] = {}
        self._local = threading.local()
        self.held_total = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_log_summary", False) or getattr(self._local, "busy", False):
            return True  # our own summary, or a line written while we decide
        self._local.busy = True
        try:
            return self._decide(record)
        except Exception:  # noqa: silent — a record we cannot read goes on to emit(), which reports it as it always did
            return True
        finally:
            self._local.busy = False

    def _decide(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self.registry is not None:
            self.registry.note(record, message)
        if record.levelno >= logging.CRITICAL or _exempt(record) or not flag("LOG_GROUPING", True):
            return True
        key = (record.name, record.pathname, record.lineno, _shape(message))
        now = self._clock()
        with self._lock:
            group = self._groups.get(key)
            if group is None or now - group.last_at > GROUP_RESET_AFTER_S:
                if group is None and len(self._groups) >= _MAX_GROUPS:
                    self._evict_oldest()
                self._groups[key] = _Group(record, message, now)
                return True
            group.last_at = now
            if now - group.last_emit >= group.interval:
                held, ids, span = group.held, len(group.ids), now - group.last_emit
                self._reopen(group, now)
                if held:
                    record.msg = f"{message} [+{held} similar in the last {_minutes(span)}, {ids} distinct ids]"
                    record.args = None
                    record.repeat_count = held
                    record.distinct_ids = ids
                return True
            group.held += 1
            self.held_total += 1
            if len(group.ids) < _MAX_IDS:
                group.ids.update(_UUID_RE.findall(message))
            group.last_message = message
            return False

    @staticmethod
    def _reopen(group: _Group, now: float) -> None:
        group.held = 0
        group.ids = set()
        group.last_emit = now
        group.interval = min(group.interval * 2, GROUP_MAX_WINDOW_S)

    def _evict_oldest(self) -> None:
        oldest = min(self._groups, key=lambda k: self._groups[k].last_at)
        del self._groups[oldest]

    def flush(self, force: bool = False) -> int:
        """One line for every kind whose window closed with lines held back
        (all of them with force=True, at shutdown). Returns how many."""
        now = self._clock()
        due = []
        with self._lock:
            for key, group in list(self._groups.items()):
                if group.held and (force or now - group.last_emit >= group.interval):
                    due.append((group.logger, group.level, group.event, group.last_message,
                                group.held, len(group.ids), now - group.last_emit))
                    self._reopen(group, now)
                elif not group.held and now - group.last_at > GROUP_RESET_AFTER_S:
                    del self._groups[key]
        for name, level, event, last_message, held, ids, span in due:
            logging.getLogger(name).log(
                level,
                f"{last_message} [×{held} similar in the last {_minutes(span)}, {ids} distinct ids]",
                extra={"_log_summary": True, "event": event, "repeat_count": held, "distinct_ids": ids},
            )
        return len(due)


def _minutes(seconds: float) -> str:
    return f"{max(1, round(seconds / 60))} min"


class RateCapFilter(logging.Filter):
    """At most `per_second` lines a second (bursts to `burst`); the rest are
    held back and counted. ERROR, security and our own summaries always pass."""

    def __init__(self, per_second: float = 100.0, burst: float = 200.0, clock: Callable[[], float] = time.monotonic):
        super().__init__()
        self.per_second = per_second
        self.burst = burst
        self._clock = clock
        self._lock = threading.Lock()
        self._tokens = burst
        self._updated = clock()
        self.dropped = 0
        self.dropped_total = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR or getattr(record, "_log_summary", False) or _exempt(record):
            return True
        now = self._clock()
        with self._lock:
            self._tokens = min(self.burst, self._tokens + (now - self._updated) * self.per_second)
            self._updated = now
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            self.dropped += 1
            self.dropped_total += 1
            return False

    def flush(self) -> int:
        with self._lock:
            dropped, self.dropped = self.dropped, 0
        if dropped:
            logging.getLogger(__name__).warning(
                f"[logs] safety valve held back {dropped} lines (cap {self.per_second:g}/s)",
                extra={"_log_summary": True, "event": "logs.capped", "dropped": dropped},
            )
        return dropped


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:  # noqa: silent — read before logging exists; a typo keeps the default
        return default


REGISTRY = EventRegistry()
GROUPING = GroupingFilter(REGISTRY)
CAP = RateCapFilter(per_second=_env_number("LOG_RATE_CAP", 100.0))
FLUSH_EVERY_S = 30.0


def log_health_snapshot() -> dict:
    """/health/detail → logs."""
    return {
        "format": log_format(),
        "grouping": bool(flag("LOG_GROUPING", True)),
        "held_back_total": GROUPING.held_total,
        "capped_total": CAP.dropped_total,
        "today": REGISTRY.snapshot(),
    }


async def run_flush_forever(interval: float = FLUSH_EVERY_S) -> None:
    """Writes the summaries whose window closed. Started with the app."""
    log = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(interval)
        try:
            GROUPING.flush()
            CAP.flush()
        except Exception as e:
            log.error(f"[logs] flush failed: {type(e).__name__}: {e}")


def _build_handler(fmt: str) -> logging.Handler:
    if fmt == "json":
        handler: logging.Handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(TEXT_FORMAT, datefmt=TEXT_DATEFMT))
    # Order matters: the id first, then grouping, then the valve.
    handler.addFilter(RequestIdFilter())
    handler.addFilter(GROUPING)
    handler.addFilter(CAP)
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
