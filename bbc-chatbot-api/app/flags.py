"""Runtime flags — one place to read every switch, so a rollback is a row, not a deploy.

Order of truth for `flag(name, default)`:
  1. `app_settings.flags` (jsonb, the singleton row id=1), refreshed every 30 s
     by a background task — the owner flips a switch there and every process
     sees it within half a minute, without a restart;
  2. the environment variable with the same name (upper case) — what Railway
     sets; changing one there restarts the service;
  3. `default` — always today's behaviour (rule R5 of the plan).

`flag()` itself never touches the network: it reads the in-memory snapshot the
refresher keeps. A table or column that does not exist yet (migration not
applied) is a normal state: one warning, then the environment decides.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 30.0

_snapshot: dict[str, Any] = {}
_loaded_at: Optional[float] = None
_warned_missing = False

# Counters for /health/detail. A refresh that fails is counted, never silent:
# "no flags in the DB" and "could not read the DB" are different answers.
FLAGS_HEALTH: dict[str, Any] = {
    "refresh_ok": 0,
    "refresh_errors": 0,
    "source_missing": False,
    "loaded_at": None,
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _coerce(raw: Any, default: Any) -> Any:
    """Read `raw` as the type of `default`. A value that does not parse keeps the default."""
    if raw is None:
        return default
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        return default
    if isinstance(default, int):
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError) as e:
            logger.warning(f"[flags] not an int, keeping default ({type(e).__name__}): {raw!r}")
            return default
    if isinstance(default, float):
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError) as e:
            logger.warning(f"[flags] not a number, keeping default ({type(e).__name__}): {raw!r}")
            return default
    return str(raw).strip()


def flag(name: str, default: Any) -> Any:
    """The value of switch `name`, typed like `default`."""
    key = name.strip().upper()
    if key in _snapshot:
        return _coerce(_snapshot[key], default)
    env = os.environ.get(key)
    if env is not None:
        return _coerce(env, default)
    return default


def snapshot() -> dict[str, Any]:
    """What the database currently says (for /health/detail and tests)."""
    return dict(_snapshot)


def _apply(raw: Any) -> None:
    global _snapshot, _loaded_at
    flags = raw if isinstance(raw, dict) else {}
    # A JSON null is no value: the environment, then the default, decide.
    _snapshot = {str(k).strip().upper(): v for k, v in flags.items() if v is not None}
    _loaded_at = time.monotonic()
    FLAGS_HEALTH["loaded_at"] = time.time()


_MISSING_CODES = ("42P01", "42703", "PGRST204", "PGRST205")
_MISSING_MARKERS = ("does not exist", "could not find", "42p01", "42703", "pgrst204", "pgrst205")


def _is_missing_source(exc: Exception) -> bool:
    """The table or the column is not there yet — a migration not applied, not an outage.

    By the database's own code when the error carries one (any other code is
    a real failure, said every time); the text is read only without one."""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.strip():
        return code.strip().upper() in _MISSING_CODES
    text = str(exc).lower()
    mentions_source = "flags" in text or "app_settings" in text
    return mentions_source and any(m in text for m in _MISSING_MARKERS)


async def refresh() -> bool:
    """Reload the snapshot from app_settings. True = read OK (even if empty)."""
    global _warned_missing
    from app.db.supabase import _run_sync, get_client

    try:
        res = await _run_sync(
            lambda: get_client().table("app_settings").select("flags").eq("id", 1).limit(1).execute(),
            label="flags",
        )
    except Exception as e:
        FLAGS_HEALTH["refresh_errors"] += 1
        if _is_missing_source(e):
            FLAGS_HEALTH["source_missing"] = True
            if not _warned_missing:
                _warned_missing = True
                logger.warning(
                    "[flags] app_settings.flags not readable (migration not applied?) — "
                    f"environment decides: {type(e).__name__}"
                )
            return False
        logger.warning(f"[flags] refresh failed, keeping the last snapshot: {type(e).__name__}: {e}")
        return False

    rows = getattr(res, "data", None) or []
    _apply(rows[0].get("flags") if rows else {})
    FLAGS_HEALTH["refresh_ok"] += 1
    FLAGS_HEALTH["source_missing"] = False
    return True


async def _refresh_forever() -> None:
    while True:
        try:
            await refresh()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            FLAGS_HEALTH["refresh_errors"] += 1
            logger.warning(f"[flags] refresher iteration failed: {type(e).__name__}: {e}")
        await asyncio.sleep(REFRESH_SECONDS)


def start_refresher() -> asyncio.Task:
    """Started from the app's startup hook; cancelled on shutdown."""
    return asyncio.create_task(_refresh_forever(), name="flags_refresher")


def _reset_for_tests() -> None:
    global _snapshot, _loaded_at, _warned_missing
    _snapshot = {}
    _loaded_at = None
    _warned_missing = False
    FLAGS_HEALTH.update(refresh_ok=0, refresh_errors=0, source_missing=False, loaded_at=None)
