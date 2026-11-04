"""The client's IP address, read in exactly one place.

Before this module, four call sites read it themselves and disagreed on the
order of headers. `CLIENT_IP_HEADER` (app/flags.py) chooses the source:

- `legacy` (default) — today's behaviour, unchanged, per call site:
  the metadata sites read `cf-connecting-ip`, then the first `X-Forwarded-For`
  element, then the socket peer; the rate limiter and the integration gate read
  `X-Forwarded-For`, then the peer (`prefer_cf=False`).
- `xff-hops:N` — the Nth `X-Forwarded-For` element counted from the RIGHT,
  N being the number of proxies that append to it (1 = the edge alone). A
  client can prepend anything; it cannot write to the right of what the edge
  appends, so this is the mode for an edge that appends instead of replacing.
- any other value is a header name (e.g. `x-real-ip`) set by the platform's
  edge. It is switched on only after the production test (step 0.8 of the
  plan) shows the edge sets it and a client cannot.

`legacy` stays the default because it is what production runs today: moving
every visitor to a source the edge has not been shown to set would put them
all behind one proxy address and one rate-limit bucket. Until the self-test
below says the edge cannot be forged, nothing keys a lockout on the IP alone
(login_guard falls back to the email while `ip_trusted()` is False).

A configured header that is missing from a request falls back to `legacy`
and is counted: if it were trusted blindly and the edge stopped sending it,
every visitor would share the proxy's address and one rate-limit bucket.

`IP_TRUSTED` is cleared by the hourly self-test (app/services/ip_selftest.py)
when a spoofed value comes back. While it is False nothing may ban by IP, and
the legacy order reads the rightmost X-Forwarded-For element instead of the
forgeable leftmost one. `ip_verified` is True only after a test has passed;
a skipped or failed test leaves it unset (None or False), never True.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.flags import flag

logger = logging.getLogger(__name__)

CLIENT_IP_HEALTH: dict[str, Any] = {
    "header_missing": 0,
    "ip_trusted": True,
    # None until a self-test has actually run and passed: "not proven
    # forgeable" (ip_trusted) is not "proven safe" (ip_verified).
    "ip_verified": None,
    "spoofable_fallback": 0,
    "last_selftest": None,
}
_alerted_missing = False


def _peer(request: Any) -> str:
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or ""


def _first_forwarded(request: Any) -> str:
    return (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()


def _from_right(request: Any, hops: int) -> str:
    parts = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    if hops < 1 or len(parts) < hops:
        return ""
    return parts[-hops]


def _legacy(request: Any, prefer_cf: bool) -> str:
    if prefer_cf:
        cf = (request.headers.get("cf-connecting-ip") or "").strip()
        if cf:
            return cf
    return _first_forwarded(request) or _peer(request)


def _alert_missing_once(header: str) -> None:
    global _alerted_missing
    if _alerted_missing:
        return
    _alerted_missing = True
    logger.error(
        f"[client-ip] configured header {header!r} missing from a request — "
        "using the legacy order; check the edge before relying on it"
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as e:
        logger.warning(f"[client-ip] no running loop for the ops alert: {e}")
        return
    from app.services.email import send_ops_alert_email

    loop.create_task(
        send_ops_alert_email(
            "Client IP header missing",
            f"CLIENT_IP_HEADER={header} but a request arrived without it. "
            "The legacy header order is used until this is resolved.",
        )
    )


def client_ip(request: Optional[Any], *, prefer_cf: bool = True) -> str:
    """The address to rate-limit, record and (later) ban by. '' if unknown."""
    if request is None:
        return ""
    mode = str(flag("CLIENT_IP_HEADER", "legacy")).strip().lower()
    if mode in ("", "legacy"):
        if not ip_trusted():
            # The self-test saw a forged leftmost X-Forwarded-For come back:
            # the edge appends instead of replacing. Its own entry is the
            # rightmost one, which a client cannot write past, so the rate
            # limiter and the login backoff key on that until the edge is
            # fixed or a mode is configured.
            right = _from_right(request, 1)
            if right:
                CLIENT_IP_HEALTH["spoofable_fallback"] += 1
                return right
        return _legacy(request, prefer_cf)
    if mode.startswith("xff-hops:"):
        try:
            hops = int(mode.split(":", 1)[1])
        except ValueError:  # noqa: silent — a malformed hop count reads as 0: legacy order, counted as header_missing
            hops = 0
        value = _from_right(request, hops)
    else:
        value = (request.headers.get(mode) or "").split(",")[0].strip()
    if value:
        return value
    CLIENT_IP_HEALTH["header_missing"] += 1
    _alert_missing_once(mode)
    return _legacy(request, prefer_cf)


def ip_trusted() -> bool:
    """False once the self-test has seen a forged IP come back."""
    return bool(CLIENT_IP_HEALTH["ip_trusted"])


def ip_verified() -> bool:
    """True only after a self-test ran and the forged IP did NOT come back."""
    return CLIENT_IP_HEALTH["ip_verified"] is True


def _reset_for_tests() -> None:
    global _alerted_missing
    _alerted_missing = False
    CLIENT_IP_HEALTH.update(header_missing=0, ip_trusted=True, ip_verified=None,
                            spoofable_fallback=0, last_selftest=None)
