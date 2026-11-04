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
import re
import time
from typing import Optional

from app.flags import flag
from app.security.behavior import WIDGET_BAIT_PATH

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


_CHAT_OWNED = re.compile(r"^/api/chat/(typing|session|messages|agent-typing|stream|status)/")
_ENUM = re.compile(r"^/api/chat/(status/([^/]+)|visitor/([^/]+)/active-conversation)$")
_CONV_IN_PATH = re.compile(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:/|$)")
_OPS_STATUS = "/api/agents/ops-status"
_CRM_ID = re.compile(r"^/api/leads/[^/]+/crm-id$")
_STREAM_MAX = 5
_OPEN_STREAMS: dict[str, int] = {}
_LAST_CHAT_POST: dict[str, float] = {}
_SEEN_FOREIGN_ORIGINS: set[str] = set()


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
        stream_ip = ctx.get("stream_ip")
        if not (ctx.get("watch_404") or ctx.get("watch_owned")):
            try:
                await self.app(scope, receive, send)
            finally:
                if stream_ip:
                    _OPEN_STREAMS[stream_ip] = max(0, _OPEN_STREAMS.get(stream_ip, 1) - 1)
            return

        status_box = {"status": 0}

        async def _send(message):
            if message.get("type") == "http.response.start":
                status_box["status"] = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            if stream_ip:
                _OPEN_STREAMS[stream_ip] = max(0, _OPEN_STREAMS.get(stream_ip, 1) - 1)
        try:
            if status_box["status"] == 404 and ctx.get("watch_404"):
                await self._scan_strike(ctx)
            if status_box["status"] in (403, 404) and ctx.get("watch_owned"):
                await self._owned_strike(ctx)
        except Exception as e:  # noqa: silent — the strike count is best-effort, the reply already went
            logger.debug(f"[ban-guard] strike failed: {type(e).__name__}")

    async def _should_block(self, scope, ctx: dict) -> bool:
        if not flag("FAIL2BAN_ENABLED", True):
            return False
        path = scope.get("path", "")
        method = (scope.get("method") or "GET").upper()
        if method == "OPTIONS":
            return False  # a preflight is never a trap
        if path in _EXCLUDED_EXACT or _seg_match_prefix(path, _EXCLUDED_PREFIXES):
            return False
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        query = (scope.get("query_string") or b"").decode("latin-1")

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
        vid = headers.get("x-visitor-id") or _query_param(query, "vid")
        conv = (_CONV_IN_PATH.search(path) or [None, ""])[1] if _CONV_IN_PATH.search(path) else ""
        ctx.update(ip=ip, store=store, vid=vid, conv=conv)

        # The two that never wait (PR-15): the bundle's decoy key and the
        # widget's bait route. Their bans are refused even in shadow.
        if not staff and ip and await store.is_banned(f"always:{ip}"):
            FAIL2BAN_HEALTH["blocked"] += 1
            return True

        # Already banned? (staff with a valid session is never locked out by IP)
        if ip and not staff and await store.is_banned(ip):
            return self._enforce("existing", ip)

        if _carries_decoy_key(headers, scope.get("query_string", b"")):
            await self._trip(store, ip, "decoy_key", _BAN_WEEK, alert=True, staff=staff,
                             mode=self._always_mode(headers), ctx=ctx)
            return False

        if path == WIDGET_BAIT_PATH:
            await self._trip(store, ip, "widget_bait", _BAN_WEEK, alert=True, staff=staff,
                             mode=self._always_mode(headers), ctx=ctx)
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

        # ── PR-15: behaviour traps (shadow until BEHAVIOR_TRAPS_ENFORCE) ──
        from app.security import behavior as bh

        raw_path = (scope.get("raw_path") or b"").decode("latin-1")
        shape = bh.request_shape_trap(method, path, raw_path, query, headers.get("user-agent", ""))
        if shape:
            await self._trip(store, ip, shape[0], shape[1], alert=False, staff=staff, mode="behavior", ctx=ctx)
            return False

        if path == _OPS_STATUS:
            await self._operator_trap("ops_status", headers, ip, revoke=True)
        elif method == "PATCH" and _CRM_ID.match(path):
            # Until Dan confirms whether the CRM calls it (0.7): counted + alert only.
            await self._operator_trap("crm_id_patch", headers, ip, revoke=False)

        if path.startswith("/api/chat") and not staff:
            origin = headers.get("origin", "")
            if method == "POST" and not origin:
                await self._trip(store, ip, "chat_no_origin", _BAN_1H, alert=False, staff=False, mode="behavior", ctx=ctx)
            elif origin and origin not in _allowed_origins():
                bh.BEHAVIOR_HEALTH["foreign_origin"] += 1
                if origin not in _SEEN_FOREIGN_ORIGINS and len(_SEEN_FOREIGN_ORIGINS) < 500:
                    _SEEN_FOREIGN_ORIGINS.add(origin)
                    await self._alert(f"chat call from a foreign Origin: {origin[:120]} ip={ip}")

            if method == "POST" and path == "/api/chat" and ip:
                now = time.monotonic()
                last = _LAST_CHAT_POST.get(ip)
                _LAST_CHAT_POST[ip] = now
                if len(_LAST_CHAT_POST) > 50_000:
                    _LAST_CHAT_POST.clear()
                if last is not None and now - last < 0.3:
                    await self._trip(store, ip, "chat_too_fast", _BAN_1H, alert=False, staff=False, mode="behavior", ctx=ctx)

            m = _ENUM.match(path)
            if m and ip:
                ident = m.group(2) or m.group(3) or ""
                n = await store.distinct(f"enum:{ip}", ident, 10 * 60)
                if n > 3:
                    await self._trip(store, ip, "enumeration", 6 * 3600, alert=False, staff=False, mode="behavior", ctx=ctx)

            if path.startswith("/api/chat/stream/") and ip:
                _OPEN_STREAMS[ip] = _OPEN_STREAMS.get(ip, 0) + 1
                ctx["stream_ip"] = ip
                if _OPEN_STREAMS[ip] > _STREAM_MAX:
                    await self._trip(store, ip, "stream_flood", _BAN_1H, alert=False, staff=False, mode="behavior", ctx=ctx)

            if _CHAT_OWNED.match(path) and ip:
                ctx["watch_owned"] = True

        if not staff and ip and (path in _SCAN_PATH_EXACT or _seg_match_prefix(path, _SCAN_PATH_PREFIXES)):
            ctx.update(watch_404=True)
        return False

    async def _scan_strike(self, ctx: dict) -> None:
        store, ip = ctx["store"], ctx["ip"]
        n = await store.strike(f"scan:{ip}", _SCAN_WINDOW)
        if n >= _SCAN_THRESHOLD:
            await self._trip(store, ip, "scan", _BAN_1H, alert=False, staff=False)

    async def _owned_strike(self, ctx: dict) -> None:
        """Two 403/404s on routes a visitor owns, inside 10 minutes: someone
        is trying conversation ids that are not theirs."""
        store, ip = ctx["store"], ctx["ip"]
        n = await store.strike(f"owned:{ip}", 10 * 60)
        if n >= 2:
            await self._trip(store, ip, "ownership_probe", _BAN_1H, alert=False, staff=False, mode="behavior", ctx=ctx)

    async def _operator_trap(self, trap: str, headers: dict, ip: str, *, revoke: bool) -> None:
        """A route the panel never calls, hit with a staff session."""
        from app.security import behavior as bh

        bh.BEHAVIOR_HEALTH["operator_traps"][trap] = bh.BEHAVIOR_HEALTH["operator_traps"].get(trap, 0) + 1
        sub = _jwt_sub(headers)
        enforce = revoke and bh.enforced("BEHAVIOR_TRAPS_ENFORCE")
        bh.record(trap, ip=ip, enforced_now=enforce)
        if enforce and sub:
            from app.security.ban_store import get_store
            store = get_store()
            if store is not None:
                await store.revoke_sessions(sub)
        await self._alert(f"operator trap {trap} user={sub or '-'} ip={ip} "
                          f"{'sessions revoked' if enforce and sub else 'counted'}")

    def _always_mode(self, headers: dict) -> str:
        """The decoy key and the widget bait ban without waiting — but only
        when the hit cannot have been planted and the IP is proven:
          - a cross-site no-cors fetch (an <img> on someone else's page, to
            get OUR visitors banned) is counted, never banned;
          - the IP must come from a self-test that PASSED; a skipped test
            (no PUBLIC_API_URL) or a spoofable edge only records and alerts.
        No visitor-id ban: the id is client-supplied, so anyone who knew one
        could have it banned for a year (review fix)."""
        from app.security.client_ip import CLIENT_IP_HEALTH

        if _is_poisoned(headers):
            FAIL2BAN_HEALTH["poisoned_skipped"] += 1
            return "alert_only"
        if not CLIENT_IP_HEALTH.get("ip_verified"):
            return "alert_only"
        return "always"

    async def _alert(self, text: str) -> None:
        await _ops_alert(text)

    async def _trip(self, store, ip: str, jail: str, seconds: int, *, alert: bool, staff: bool,
                    mode: str = "fail2ban", ctx: Optional[dict] = None) -> None:
        await trip(store, ip, jail, seconds, alert=alert, staff=staff, mode=mode, ctx=ctx)

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
        """An identity already banned came back. Refused while either kind of
        enforcement is on; turning both off is a full rollback."""
        if flag("FAIL2BAN_ENFORCE", False) or flag("BEHAVIOR_TRAPS_ENFORCE", False):
            FAIL2BAN_HEALTH["blocked"] += 1
            return True
        return False

    async def _deny(self, send) -> None:
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"detail":"Forbidden"}'})


def _query_param(query: str, name: str) -> str:
    from urllib.parse import parse_qs
    try:
        return (parse_qs(query).get(name) or [""])[0]
    except ValueError:  # noqa: silent — a malformed query string simply has no such parameter
        return ""


def _allowed_origins() -> set[str]:
    from config.settings import settings
    return {o.rstrip("/") for o in (settings.cors_origins or [])}


def _jwt_sub(headers: dict) -> str:
    auth = headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return ""
    from config.settings import settings
    if not settings.jwt_secret:
        return ""
    import jwt as _jwt
    try:
        payload = _jwt.decode(auth[7:].strip(), settings.jwt_secret, algorithms=["HS256"])
    except _jwt.InvalidTokenError:  # noqa: silent — no valid session, no user to name
        return ""
    return "" if payload.get("scope") else str(payload.get("sub") or "")


# The tripwire's actions, module-level so a login failure (note_login_failure)
# uses them without building a middleware around nothing.
async def trip(store, ip: str, jail: str, seconds: int, *, alert: bool, staff: bool,
               mode: str = "fail2ban", ctx: Optional[dict] = None) -> None:
    """A tripwire fired. Staff (valid JWT) is never banned — it alerts.

    mode: "fail2ban" (FAIL2BAN_ENFORCE), "behavior" (PR-15,
    BEHAVIOR_TRAPS_ENFORCE), "always" (the decoy key, the widget bait —
    no shadow), "alert_only" (unproven IP or cross-site: counted, never banned)."""
    FAIL2BAN_HEALTH["by_jail"][jail] = FAIL2BAN_HEALTH["by_jail"].get(jail, 0) + 1
    if staff:
        FAIL2BAN_HEALTH["staff_alerts"] += 1
        await _ops_alert(f"staff tripwire jail={jail} ip={ip}")
        return
    if mode == "always":
        enforce = True
    elif mode == "alert_only":
        enforce = False
        if alert:
            await _ops_alert(f"jail={jail} ip={ip} not banned (unproven IP or cross-site)")
    elif mode == "behavior":
        from app.security import behavior as bh
        enforce = bh.enforced("BEHAVIOR_TRAPS_ENFORCE")
        c = ctx or {}
        bh.record(jail, ip=ip, conversation_id=c.get("conv", ""), visitor_id=c.get("vid", ""),
                  enforced_now=enforce)
    else:
        enforce = bool(flag("FAIL2BAN_ENFORCE", False))
    if not enforce:
        FAIL2BAN_HEALTH["would_ban"] += 1
        return
    FAIL2BAN_HEALTH["banned"] += 1
    if mode == "always":
        # Recorded in security_bans under always:<ip>, so the panel can
        # unban it like any other.
        if ip:
            await _ban_identity(store, f"always:{ip}", jail, seconds, alert=alert)
        return
    await _ban_identity(store, ip, jail, seconds, alert=alert)


async def _ban_identity(store, ip: str, jail: str, seconds: int, *, alert: bool) -> None:
    if not ip:
        return
    eff, created = seconds, True
    try:
        eff, created = await store.ban(ip, jail, seconds)
        eff = eff or seconds
    except Exception as e:  # noqa: silent — a failed ban write must not block the response
        logger.debug(f"[ban-guard] ban write failed: {type(e).__name__}")
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
        text = str(e).lower()
        if "security_bans" in text and ("does not exist" in text or "could not find" in text or "42p01" in text):
            _table_missing = True
            FAIL2BAN_HEALTH["table_missing"] = True
            logger.warning(f"[ban-guard] security_bans missing — history off until 042 is applied: {type(e).__name__}")
            return
        FAIL2BAN_HEALTH["row_errors"] += 1
        logger.warning(f"[ban-guard] security_bans write failed: {type(e).__name__}: {e}")


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
    _OPEN_STREAMS.clear()
    _LAST_CHAT_POST.clear()
    _SEEN_FOREIGN_ORIGINS.clear()
    FAIL2BAN_HEALTH.update(degraded=False, would_ban=0, banned=0, blocked=0, staff_alerts=0,
                           rows_written=0, row_errors=0, table_missing=False,
                           poisoned_skipped=0, ban_already_held=0, by_jail={})
