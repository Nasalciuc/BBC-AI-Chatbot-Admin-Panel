"""Retention (PR-14, owner's decision ⚑12).

  pipeline_runs, ai_calls, agent_activity_log, agent_ready_log → 13 months
  invite_tokens used or expired more than 30 days ago         → deleted
  blocklist entries that have expired                         → removed
  conversations and messages                                  → the owner's policy; untouched here

RETENTION_ENABLED off (the default): the cron only COUNTS what it would
delete and reports it. On: it deletes in batches of BATCH ids, at most
MAX_BATCHES batches per table per run, so a first run on a year of history
is spread over several nights instead of one long lock.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.db.supabase import _run_sync, get_client
from app.flags import flag

logger = logging.getLogger(__name__)

# 100 ids per delete: the ids travel in the URL (in.(…)); 500 UUIDs are
# ~18 KB and risk a 414 from the proxy (review fix).
BATCH = 100
MAX_BATCHES = 50
THIRTEEN_MONTHS = timedelta(days=396)

RETENTION_HEALTH: dict = {"last_run": None, "enabled": False, "tables": {}}

# table → (timestamp column, age)
_LOGS = {
    "pipeline_runs": ("created_at", THIRTEEN_MONTHS),
    "ai_calls": ("created_at", THIRTEEN_MONTHS),
    "agent_activity_log": ("happened_at", THIRTEEN_MONTHS),
    "agent_ready_log": ("changed_at", THIRTEEN_MONTHS),
}


def _filters(table: str, now: datetime):
    """A function adding this table's 'old enough' filter to a query."""
    if table in _LOGS:
        col, age = _LOGS[table]
        cutoff = (now - age).isoformat()
        return lambda q: q.lt(col, cutoff)
    if table == "invite_tokens":
        # used, or expired, more than 30 days ago (review fix: a token used
        # early stayed until 30 days after its expiry). Z form: no "+" to
        # escape inside the or=(...) filter.
        cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return lambda q: q.or_(f"used_at.lt.{cutoff},expires_at.lt.{cutoff}")
    if table == "blocklist":
        cutoff = now.isoformat()
        return lambda q: q.lt("expires_at", cutoff)  # NULL (permanent) never matches
    raise ValueError(table)


TABLES = (*_LOGS.keys(), "invite_tokens", "blocklist")


async def _ids(table: str, add_filter, limit: int) -> list:
    res = await _run_sync(
        lambda: add_filter(get_client().table(table).select("id")).limit(limit).execute(),
        label=f"retention:{table}",
    )
    return [r["id"] for r in (getattr(res, "data", None) or [])]


async def run_once() -> dict:
    now = datetime.now(timezone.utc)
    enabled = bool(flag("RETENTION_ENABLED", False))
    out: dict = {}
    for table in TABLES:
        add_filter = _filters(table, now)
        try:
            if not enabled:
                res = await _run_sync(
                    lambda: add_filter(get_client().table(table).select("id", count="exact")).limit(1).execute(),  # type: ignore[arg-type]
                    label=f"retention:{table}",
                )
                out[table] = {"would_delete": getattr(res, "count", None)}
                continue
            deleted = 0
            for _ in range(MAX_BATCHES):
                ids = await _ids(table, add_filter, BATCH)
                if not ids:
                    break
                # The age filter again on the delete: a row refreshed since it
                # was selected (a blocklist entry given a new expiry keeps its
                # id) is no longer eligible and stays (review fix).
                res = await _run_sync(
                    lambda ids=ids: add_filter(get_client().table(table).delete().in_("id", ids)).execute(),
                    label=f"retention:{table}",
                )
                gone = getattr(res, "data", None)
                deleted += len(gone) if isinstance(gone, list) else len(ids)
                if len(ids) < BATCH:
                    break
            out[table] = {"deleted": deleted}
        except Exception as e:
            logger.warning(f"[retention] {table} skipped: {type(e).__name__}: {e}")
            out[table] = {"error": type(e).__name__}
    RETENTION_HEALTH.update(last_run=now.isoformat(), enabled=enabled, tables=out)
    return out
