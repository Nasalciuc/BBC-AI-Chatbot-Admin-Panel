"""fail2ban as pure ASGI middleware (PR-11).

Not BaseHTTPMiddleware: that buffers the response and breaks SSE (the panel and
widget streams). This is a raw ASGI app wrapping the real app; it inspects the
request, decides ban/allow, and either short-circuits a generic 404 or passes
through untouched.

Fail-open by design: if Redis is down or IP_TRUSTED is false (the edge lets a
client forge its IP, PR-1 self-test), nobody is banned and /health reports
degraded — a tripwire must never lock out real customers.

A tripwire request itself gets its normal answer (the canary body for /.env,
the ordinary 404, the ordinary 401 for Basic): the attacker learns nothing.
The ban takes effect from the next request, which gets a generic 403.

FAIL2BAN_ENFORCE off (the merge default, 3 shadow days): jails are evaluated
and counted but nothing is blocked.

Never banned: a request with a valid JWT (staff — its tripwires alert), the
exact IPs in BAN_IGNORE_IPS (office, CRM), the excluded paths.
Chat-body bans are NOT here (they need the parsed body); they stay on the
existing blocklist. Session revocation (revoked_before) lives here too but is
read in auth.get_current_user (sync path).
"""

from __future__ import annotations

import logging
from typing import Optional

from app.flags import flag

logger = logging.getLogger(__name__)

FAIL2BAN_HEALTH = {
    "degraded": False,
    "would_ban": 0,
    "banned": 0,
    "blocked": 0,
    "staff_alerts": 0,
    "rows_written": 0,
    "row_errors": 0,
    "table_missing": False,
    "poisoned_skipped": 0,
    "ban_already_held": 0,
    "ban_errors": 0,  # the ban store failed: nothing was banned or recorded
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
_BAN_1H = 3600
_SCAN_THRESHOLD = 5
_SCAN_WINDOW = 5 * 60
# 404s here, without a valid JWT, are a scan.
_SCAN_PATH_PREFIXES = ("/api/",)
_SCAN_PATH_EXACT = {"/openapi.json", "/docs", "/redoc"}
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
    """A browser request made by another site (an <img src=.../.env>, a
    fetch(), a link or a redirect someone plants to get our visitors banned)
    is counted, never banned. Any Sec-Fetch-Mode: the browser sends the
    request in every mode, CORS only hides the answer. Scanners (curl & co.)
    send no Sec-Fetch-* at all, so they are not spared by this."""
    if headers.get("sec-fetch-site", "") == "cross-site":
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
        ctx: dict = {}
        try:
            blocked = await self._should_block(scope, ctx)
        except Exception as e:  # noqa: silent — a tripwire must never break a real request
            logger.debug(f"[ban-guard] skipped: {type(e).__name__}: {e}")
            blocked = False
            ctx = {}
        if blocked:
            await self._deny(send)
            return
        if not ctx.get("watch_404"):
            await self.app(scope, receive, send)
            return

        status_box = {"status": 0}

        async def _send(message):
            if message.get("type") == "http.response.start":
                status_box["status"] = message.get("status", 0)
            await send(message)

        await self.app(scope, receive, _send)
        if status_box["status"] == 404:
            try:
                await self._scan_strike(ctx)
            except Exception as e:  # noqa: silent — the scan count is best-effort, the reply already went
                logger.debug(f"[ban-guard] scan strike failed: {type(e).__name__}")

    async def _should_block(self, scope, ctx: dict) -> bool:
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
        if ip and ip in _ignored_ips():
            return False
        staff = _has_valid_jwt(headers)

        # Already banned? (staff with a valid session is never locked out by IP)
        if ip and not staff and await store.is_banned(ip):
            return self._enforce("existing", ip)

        # The decoy key from the bundle, anywhere in the request → a week.
        if _carries_decoy_key(headers, scope.get("query_string", b"")):
            await self._trip(store, ip, "decoy_key", _BAN_WEEK, alert=True, staff=staff)
            return False

        # A Basic header: the old door (PR-1 removed it).
        if headers.get("authorization", "").startswith("Basic "):
            await self._trip(store, ip, "basic", _BAN_24H, alert=True, staff=staff)
            return False

        hit = _classify_path(path)
        if hit:
            jail, secs, alert = hit
            if _is_poisoned(headers):
                FAIL2BAN_HEALTH["poisoned_skipped"] += 1
                return False
            await self._trip(store, ip, jail, secs, alert=alert, staff=staff)
            return False

        if not staff and ip and (path in _SCAN_PATH_EXACT or _seg_match_prefix(path, _SCAN_PATH_PREFIXES)):
            ctx.update(watch_404=True, ip=ip, store=store)
        return False

    async def _scan_strike(self, ctx: dict) -> None:
        store, ip = ctx["store"], ctx["ip"]
        n = await store.strike(f"scan:{ip}", _SCAN_WINDOW)
        if n >= _SCAN_THRESHOLD:
            await self._trip(store, ip, "scan", _BAN_1H, alert=False, staff=False)

    async def _trip(self, store, ip: str, jail: str, seconds: int, *, alert: bool, staff: bool) -> None:
        await trip(store, ip, jail, seconds, alert=alert, staff=staff)

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
        """An identity already banned came back."""
        if flag("FAIL2BAN_ENFORCE", False):
            FAIL2BAN_HEALTH["blocked"] += 1
            return True
        return False

    async def _deny(self, send) -> None:
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail":"Forbidden"}'})


# The tripwire's actions, module-level so a login failure (note_login_failure)
# uses them without building a middleware around nothing.
async def trip(store, ip: str, jail: str, seconds: int, *, alert: bool, staff: bool) -> None:
    """A tripwire fired. Staff (valid JWT) is never banned — it alerts."""
    FAIL2BAN_HEALTH["by_jail"][jail] = FAIL2BAN_HEALTH["by_jail"].get(jail, 0) + 1
    if staff:
        FAIL2BAN_HEALTH["staff_alerts"] += 1
        await _ops_alert(f"staff tripwire jail={jail} ip={ip}")
        return
    if flag("FAIL2BAN_ENFORCE", False):
        FAIL2BAN_HEALTH["banned"] += 1
        await _ban_identity(store, ip, jail, seconds, alert=alert)
    else:
        FAIL2BAN_HEALTH["would_ban"] += 1


async def _ban_identity(store, ip: str, jail: str, seconds: int, *, alert: bool) -> None:
    if not ip:
        return
    try:
        eff, created = await store.ban(ip, jail, seconds)
    except Exception as e:
        # No ban was set: no row and no alert claim one. Counted apart from a
        # ban another request holds; the response is never blocked by it.
        FAIL2BAN_HEALTH["ban_errors"] += 1
        logger.warning(f"[ban-guard] ban write failed, nothing recorded: {type(e).__name__}")
        return
    eff = eff or seconds
    if not created:
        # A parallel request already banned this identity: one row, one
        # alert, one doubling per ban.
        FAIL2BAN_HEALTH["ban_already_held"] += 1
        return
    await record_ban_row(ip, jail, eff)
    if alert:
        await _ops_alert(f"jail={jail} ip={ip} banned {eff}s")


async def _ops_alert(text: str) -> None:
    try:
        from app.services.email import send_ops_alert_email
        await send_ops_alert_email("fail2ban", text)
    except Exception as e:  # noqa: silent — the alert is best-effort
        logger.debug(f"[ban-guard] alert failed: {type(e).__name__}")


def _ignored_ips() -> set[str]:
    from config.settings import settings
    raw = getattr(settings, "ban_ignore_ips", "") or ""
    return {p.strip() for p in raw.split(",") if p.strip()}


def _has_valid_jwt(headers: dict) -> bool:
    auth = headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return False
    from config.settings import settings
    if not settings.jwt_secret:
        return False
    import jwt as _jwt
    try:
        payload = _jwt.decode(auth[7:].strip(), settings.jwt_secret, algorithms=["HS256"])
        # A limited token (PR-13: second factor still pending) is not staff.
        return not payload.get("scope")
    except _jwt.InvalidTokenError:  # noqa: silent — an invalid token simply means "not staff" here
        return False


def _carries_decoy_key(headers: dict, query_string: bytes) -> bool:
    from app.security.decoys import _decoy_keys
    keys = _decoy_keys()
    if not keys:
        return False
    hay = " ".join(headers.values()) + " " + (query_string or b"").decode("latin-1")
    return any(k in hay for k in keys)


# ── The audit trail (security_bans) ───────────────────────────
_table_missing = False


async def record_ban_row(identity: str, jail: str, seconds: int) -> None:
    """Best-effort row for the panel's Security → Bans page. A missing table
    (042 not applied) logs once and stays quiet; the ban itself is in Redis."""
    global _table_missing
    if _table_missing:
        return
    import datetime as _dt
    from app.db.supabase import _run_sync, get_client

    now = _dt.datetime.now(_dt.timezone.utc)
    row = {
        "identity": identity,
        "jail": jail,
        "last_seen_at": now.isoformat(),
        "expires_at": (now + _dt.timedelta(seconds=seconds)).isoformat(),
    }
    try:
        await _run_sync(
            lambda: get_client().table("security_bans").insert(row).execute(),
            idempotent=False,
            label="security_bans",
        )
        FAIL2BAN_HEALTH["rows_written"] += 1
    except Exception as e:
        from app.db.supabase import _error_code, _loggable_code

        # The table (042 not applied), by the database's code: the text also
        # matched a missing column of the table, which switched the history
        # off for good. The text is never logged either: it can quote the row.
        code = _error_code(e)
        if code in ("42P01", "PGRST205"):
            _table_missing = True
            FAIL2BAN_HEALTH["table_missing"] = True
            logger.warning(f"[ban-guard] security_bans missing — history off until 042 is applied: {type(e).__name__} {code}")
            return
        FAIL2BAN_HEALTH["row_errors"] += 1
        logger.warning(f"[ban-guard] security_bans write failed: {type(e).__name__} {_loggable_code(code)}".rstrip())


# ── The auth-attack jail (called from the login route) ────────
_DECOY_LOGIN_LOCALS = {"admin", "test", "root", "administrator", "support"}
_AUTH_WINDOW = 10 * 60
_AUTH_DISTINCT_EMAILS = 3


def _is_decoy_login(email: str) -> bool:
    e = (email or "").strip().lower()
    local = e.split("@", 1)[0]
    if local in _DECOY_LOGIN_LOCALS:
        return True
    from config.settings import settings
    decoy = (getattr(settings, "decoy_account_email", "") or "").strip().lower()
    return bool(decoy) and e == decoy


async def note_login_failure(ip: str, email: str, *, form_trap: bool = False) -> None:
    """A failed login. A real account's own failures stay on the email+IP pair
    (login_guard) and never ban the IP — anyone could otherwise lock out a
    staff member whose address is public. Spraying (a 4th distinct email from
    one IP in 10 min), a decoy account, or the filled company_fax trap → the
    IP is banned 1 h with an alert. Never raises."""
    try:
        if not ip or ip == "unknown" or not flag("FAIL2BAN_ENABLED", True):
            return
        from app.security.ban_store import get_store
        from app.security.client_ip import ip_trusted

        store = get_store()
        if store is None or not ip_trusted() or ip in _ignored_ips():
            return
        if form_trap or _is_decoy_login(email):
            await trip(store, ip, "auth_attack", _BAN_1H, alert=True, staff=False)
            return
        distinct = await store.note_auth_email(ip, email, _AUTH_WINDOW)
        if distinct > _AUTH_DISTINCT_EMAILS:
            await trip(store, ip, "auth_attack", _BAN_1H, alert=True, staff=False)
    except Exception as e:  # noqa: silent — the jail must never change a login's answer
        logger.debug(f"[ban-guard] auth jail skipped: {type(e).__name__}: {e}")


def _reset_for_tests() -> None:
    global _table_missing
    _table_missing = False
    FAIL2BAN_HEALTH.update(degraded=False, would_ban=0, banned=0, blocked=0, staff_alerts=0,
                           rows_written=0, row_errors=0, table_missing=False,
                           poisoned_skipped=0, ban_already_held=0, ban_errors=0, by_jail={})
