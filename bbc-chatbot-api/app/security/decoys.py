"""Decoys — tripwires that only an intruder trips, with an alert, no ban (yet).

Three server-side tripwires live here (the account and lead decoys are rows in
the database, filtered out in app/db/supabase.py):

1. The fake bundle key. The panel bundle carries a constant marked `// DECOY`.
   It authenticates nothing. Any request that carries DECOY_BUNDLE_KEY (or the
   canary key) in any header, query value or body is someone replaying what
   they scraped: alert with IP + UA, answer a generic 404.

2. The canary content paths. GET /.env and GET /api/admin/export return a few
   believable but fake records seeded with a canarytokens.org key. Fetching
   them alerts us; trying the key alerts canarytokens with the real IP.

Everything is gated by DECOYS_ENABLED and never bans — that is PR-11. An alert
is sent at most once per (reason, ip) per hour so a scanner cannot flood ops.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from config.settings import settings
from app.flags import flag
from app.security.client_ip import client_ip

logger = logging.getLogger(__name__)

DECOY_HEALTH = {"key_hits": 0, "canary_hits": 0, "alerts": 0, "alerts_suppressed": 0, "form_honeypot": 0}

_CANARY_PATHS = {"/.env", "/api/admin/export"}
# Dead-code bait paths planted in the bundles (a widget export used only by a
# test, an HTML comment in the panel). No real route serves them, so any
# request is someone who read the bundle looking for a back door.
_BAIT_PATHS = {"/api/internal/backup_v2", "/api/chat/debug-transcript"}
_last_alert: dict[tuple[str, str], float] = {}
_ALERT_COOLDOWN = 3600.0
# The IP is client-controlled until the edge is proven (client_ip.py): a
# scanner rotating X-Forwarded-For would get a fresh per-IP key every
# request. One mail per reason per 5 minutes, whatever the IP, and a bounded
# key table, keep that from flooding ops or growing without limit.
_GLOBAL_COOLDOWN = 300.0
_last_global: dict[str, float] = {}
_MAX_KEYS = 10_000


def _decoy_keys() -> list[str]:
    keys = []
    for name in ("decoy_bundle_key", "canary_aws_key_id", "canary_aws_secret"):
        v = (getattr(settings, name, "") or "").strip()
        if v and len(v) >= 8:  # an empty or trivially short value is not a signal
            keys.append(v)
    return keys


async def _alert(reason: str, ip: str, detail: str) -> None:
    DECOY_HEALTH["alerts"] += 1
    key = (reason, ip or "?")
    now = time.time()
    if now - _last_alert.get(key, 0.0) < _ALERT_COOLDOWN or now - _last_global.get(reason, 0.0) < _GLOBAL_COOLDOWN:
        DECOY_HEALTH["alerts_suppressed"] += 1
        return
    if len(_last_alert) >= _MAX_KEYS:
        for k in [k for k, t in _last_alert.items() if now - t >= _ALERT_COOLDOWN]:
            del _last_alert[k]
        if len(_last_alert) >= _MAX_KEYS:
            _last_alert.clear()
    _last_alert[key] = now
    _last_global[reason] = now
    from app.services.email import send_ops_alert_email

    await send_ops_alert_email(
        f"Decoy tripped: {reason}",
        f"{detail}\nIP: {ip or 'unknown'}",
    )


async def scan_headers_and_query(request) -> Optional[str]:
    """Cheap, body-free check used by the request logger on every request.
    PR-11 adds the body scan in its ASGI middleware."""
    if not flag("DECOYS_ENABLED", True):
        return None
    # Dead-code bait path → alert (no real route serves these); fires even when
    # no decoy keys are configured.
    if request.url.path in _BAIT_PATHS:
        ip = client_ip(request)
        DECOY_HEALTH["canary_hits"] += 1
        await _alert("bait path requested", ip,
                     f"{request.url.path} was requested. UA: {request.headers.get('user-agent', '?')}")
        return "bait_path"
    keys = _decoy_keys()
    if not keys:
        return None
    haystack = " ".join(f"{k}:{v}" for k, v in request.headers.items()) + " " + str(request.url.query or "")
    for key in keys:
        if key in haystack:
            ip = client_ip(request)
            ua = request.headers.get("user-agent", "?")
            DECOY_HEALTH["key_hits"] += 1
            logger.warning(f"[decoy] bundle/canary key replayed from {ip}")
            await _alert("bundle key replayed", ip, f"A decoy key was sent to {request.url.path}. UA: {ua}")
            return "decoy_key"
    return None


async def check_request_for_decoy_key(request) -> Optional[str]:
    """If the request carries a decoy key anywhere, alert and return a reason.
    Returns None for an ordinary request."""
    if not flag("DECOYS_ENABLED", True):
        return None
    keys = _decoy_keys()
    if not keys:
        return None
    # Headers and query first (cheap), then a bounded slice of the body.
    haystack_parts = [
        " ".join(f"{k}:{v}" for k, v in request.headers.items()),
        str(request.url.query or ""),
    ]
    try:
        body = await request.body()
        if body:
            haystack_parts.append(body[:8192].decode("utf-8", "ignore"))
    except Exception as e:  # noqa: silent — a body we cannot read simply cannot carry the key
        logger.debug(f"[decoy] body read skipped: {type(e).__name__}")
    haystack = "\n".join(haystack_parts)
    for key in keys:
        if key in haystack:
            ip = client_ip(request)
            ua = request.headers.get("user-agent", "?")
            DECOY_HEALTH["key_hits"] += 1
            logger.warning(f"[decoy] bundle/canary key replayed from {ip}")
            await _alert("bundle key replayed", ip, f"A decoy key was sent to {request.url.path}. UA: {ua}")
            return "decoy_key"
    return None


def is_canary_path(path: str) -> bool:
    return flag("DECOYS_ENABLED", True) and path in _CANARY_PATHS


def canary_body(path: str) -> str:
    """Believable, entirely fake content. The AWS key is a canarytokens.org
    token: trying it alerts us with the attacker's real IP."""
    key_id = (settings.canary_aws_key_id or "AKIAIOSFODNN7EXAMPLE").strip()
    secret = (settings.canary_aws_secret or "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY").strip()
    if path == "/.env":
        return (
            "APP_ENV=production\n"
            "DATABASE_URL=postgres://bbc_app:Tr4vel!2024@db.internal:5432/bbc\n"
            f"AWS_ACCESS_KEY_ID={key_id}\n"
            f"AWS_SECRET_ACCESS_KEY={secret}\n"
            "JWT_SECRET=5f2b9c1e8a7d4f30b6c2e9a1d7f84c35\n"
            "SMTP_PASSWORD=Postmark!ops2024\n"
        )
    # /api/admin/export — a small, plausible "backup".
    return (
        '{"export":"users","generated_at":"2026-01-14T02:11:00Z","rows":['
        '{"email":"ops-backup@buybusinessclass.com","role":"owner",'
        f'"aws_access_key_id":"{key_id}","aws_secret_access_key":"{secret}"}}]}}'
    )


async def alert_canary_fetch(request, path: str) -> None:
    ip = client_ip(request)
    ua = request.headers.get("user-agent", "?")
    DECOY_HEALTH["canary_hits"] += 1
    logger.warning(f"[decoy] canary path {path} fetched from {ip}")
    await _alert("canary path fetched", ip, f"{path} was fetched. UA: {ua}")


async def alert_role_change(actor: dict, target_id: str, changes: dict, ip: str) -> None:
    """Every role / tunnel_scope / is_active change e-mails ops at once. On
    8 Sep seven accounts were changed and three created with no trace (S2-8)."""
    if not flag("DECOYS_ENABLED", True):
        return
    from app.services.email import send_ops_alert_email

    who = actor.get("email") or actor.get("id") or "?"
    await send_ops_alert_email(
        "Access change",
        f"{who} changed access on user {target_id}: {changes}\nIP: {ip or 'unknown'}",
    )


def _reset_for_tests() -> None:
    _last_alert.clear()
    _last_global.clear()
    DECOY_HEALTH.update(key_hits=0, canary_hits=0, alerts=0, alerts_suppressed=0, form_honeypot=0)
