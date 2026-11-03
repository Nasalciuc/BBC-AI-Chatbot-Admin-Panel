"""fail2ban as pure ASGI middleware (PR-11).

Not BaseHTTPMiddleware: that buffers the response and breaks SSE (the panel and
widget streams). This is a raw ASGI app wrapping the real app; it inspects the
request, decides ban/allow, and either short-circuits a generic 404 or passes
through untouched.

Fail-open by design: if Redis is down or IP_TRUSTED is false (the edge lets a
client forge its IP, PR-1 self-test), nobody is banned and /health reports
degraded — a tripwire must never lock out real customers.

FAIL2BAN_ENFORCE off (the merge default, 3 shadow days): jails are evaluated
and counted but nothing is blocked. On → a banned identity gets a generic 404.
Chat-body bans are NOT here (they need the parsed body); they stay on the
existing blocklist. Session revocation (revoked_before) lives here too but is
read in auth.get_current_user (sync path).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from app.flags import flag

logger = logging.getLogger(__name__)

FAIL2BAN_HEALTH = {
    "degraded": False,
    "would_ban": 0,
    "banned": 0,
    "poisoned_skipped": 0,
    "by_jail": {},
}

# Generic scanner paths → instant 24h ban (honeypot). Prefix (*) or exact.
_HONEYPOT_PREFIXES = (
    "/.env", "/.git/", "/.svn/", "/.aws/", "/.ssh/", "/wp-admin", "/wp-content/",
    "/phpmyadmin", "/adminer", "/cgi-bin/", "/vendor/phpunit/", "/actuator",
    "/debug/", "/telescope/", "/_profiler/", "/backup", "/dump", "/credentials",
    "/secrets",
)
_HONEYPOT_EXACT = {
    "/.DS_Store", "/id_rsa", "/.htaccess", "/.htpasswd", "/wp-login.php",
    "/xmlrpc.php", "/server-status", "/config.json", "/config.yml",
}
# API-shaped honeypots → instant ban + immediate alert.
_API_HONEYPOT_EXACT = {
    "/api/admin/tokens", "/api/admin/password-resets", "/api/admin/set-password",
    "/api/admin/invites", "/api/admin/customers", "/api/customers", "/api/chats",
    "/api/auth/token", "/api/admin/export", "/api/admin/users/export",
    "/api/admin/backup", "/api/admin/config", "/api/admin/keys", "/api/admin/env",
    "/graphql", "/api/admin/impersonate",
}
_API_HONEYPOT_PREFIXES = ("/api/internal/", "/api/debug/", "/api/v1/", "/api/v2/")

# Never touched.
_EXCLUDED_PREFIXES = ("/api/cron/", "/api/integration/")
_EXCLUDED_EXACT = {"/health", "/health/detail", "/health/ip-echo"}

_BAN_24H = 24 * 3600
_BAN_WEEK = 7 * 24 * 3600
_SCANNER_UAS = ("sqlmap", "nikto", "nuclei", "zgrab", "masscan", "gobuster",
                "dirbuster", "ffuf", "wfuzz", "feroxbuster", "nmap", "acunetix", "wpscan")


def _seg_match_prefix(path: str, prefixes: tuple) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p) for p in prefixes)


def _classify_path(path: str) -> Optional[tuple[str, int, bool]]:
    """(jail, ban_seconds, alert) for a path that is a tripwire, else None."""
    if path in _API_HONEYPOT_EXACT or _seg_match_prefix(path, _API_HONEYPOT_PREFIXES):
        return ("honeypot_api", _BAN_24H, True)
    if path in _HONEYPOT_EXACT or _seg_match_prefix(path, _HONEYPOT_PREFIXES):
        return ("honeypot", _BAN_24H, False)
    if path.endswith((".sql", ".bak")):
        return ("honeypot", _BAN_24H, False)
    return None


def _is_poisoned(headers: dict) -> bool:
    """A cross-site no-cors fetch (someone embedding e.g. <img src=.../.env> on
    another site to get our visitors banned) is counted, never banned."""
    site = headers.get("sec-fetch-site", "")
    mode = headers.get("sec-fetch-mode", "")
    if site == "cross-site" and mode == "no-cors":
        return True
    if not any(h.startswith("sec-fetch") for h in headers):
        ref = headers.get("referer", "")
        if ref and "buybusinessclass" not in ref and "businessclass-tickets" not in ref:
            return True
    return False


class BanGuardMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            blocked = await self._should_block(scope)
        except Exception as e:  # noqa: silent — a tripwire must never break a real request
            logger.debug(f"[ban-guard] skipped: {type(e).__name__}: {e}")
            blocked = False
        if blocked:
            await self._deny(send)
            return
        await self.app(scope, receive, send)

    async def _should_block(self, scope) -> bool:
        if not flag("FAIL2BAN_ENABLED", True):
            return False
        path = scope.get("path", "")
        if path in _EXCLUDED_EXACT or _seg_match_prefix(path, _EXCLUDED_PREFIXES):
            return False
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}

        from app.security.client_ip import ip_trusted
        from app.security.ban_store import get_store

        store = get_store()
        if store is None or not ip_trusted():
            FAIL2BAN_HEALTH["degraded"] = True
            return False
        FAIL2BAN_HEALTH["degraded"] = False

        ip = self._ip_from(scope, headers)
        # Already banned?
        if ip and await store.is_banned(ip):
            return self._enforce("existing", ip)

        # A Basic header is an instant ban (the old door; PR-1 removed it).
        if headers.get("authorization", "").startswith("Basic "):
            await self._ban(store, ip, "basic", _BAN_24H, alert=True)
            return self._enforce("basic", ip)

        hit = _classify_path(path)
        if hit:
            jail, secs, alert = hit
            if _is_poisoned(headers):
                FAIL2BAN_HEALTH["poisoned_skipped"] += 1
                return False
            await self._ban(store, ip, jail, secs, alert=alert)
            return self._enforce(jail, ip)
        return False

    def _ip_from(self, scope, headers) -> str:
        class _R:
            pass
        r = _R()
        r.headers = headers
        client = scope.get("client")
        r.client = type("C", (), {"host": client[0] if client else ""})()
        from app.security.client_ip import client_ip
        return client_ip(r, prefer_cf=False) or ""

    def _enforce(self, jail: str, ip: str) -> bool:
        FAIL2BAN_HEALTH["by_jail"][jail] = FAIL2BAN_HEALTH["by_jail"].get(jail, 0) + 1
        if flag("FAIL2BAN_ENFORCE", False):
            FAIL2BAN_HEALTH["banned"] += 1
            return True
        FAIL2BAN_HEALTH["would_ban"] += 1
        return False

    async def _ban(self, store, ip: str, jail: str, seconds: int, *, alert: bool) -> None:
        if not ip:
            return
        try:
            await store.ban(ip, jail, seconds)
        except Exception as e:  # noqa: silent — a failed ban write must not block the response
            logger.debug(f"[ban-guard] ban write failed: {type(e).__name__}")
        if alert:
            try:
                from app.services.email import send_ops_alert_email
                await send_ops_alert_email("fail2ban", f"jail={jail} ip={ip} banned {seconds}s")
            except Exception as e:  # noqa: silent — the alert is best-effort
                logger.debug(f"[ban-guard] alert failed: {type(e).__name__}")

    async def _deny(self, send) -> None:
        await send({"type": "http.response.start", "status": 404,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail":"Not Found"}'})


def _reset_for_tests() -> None:
    FAIL2BAN_HEALTH.update(degraded=False, would_ban=0, banned=0, poisoned_skipped=0, by_jail={})
