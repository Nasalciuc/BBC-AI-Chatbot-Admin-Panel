"""The client's IP address, read in exactly one place.

Before this module, four call sites read it themselves and disagreed on the
order of headers. `CLIENT_IP_HEADER` (app/flags.py) chooses the source:

- `legacy` (default) — today's behaviour, unchanged, per call site:
  the metadata sites read `cf-connecting-ip`, then the first `X-Forwarded-For`
  element, then the socket peer; the rate limiter and the integration gate read
  `X-Forwarded-For`, then the peer (`prefer_cf=False`).
- any other value is a header name (e.g. `x-real-ip`) set by the platform's
  edge. It is switched on only after the production test (step 0.8 of the
  plan) shows the edge sets it and a client cannot.

A configured header that is missing from a request falls back to `legacy`
and is counted: if it were trusted blindly and the edge stopped sending it,
every visitor would share the proxy's address and one rate-limit bucket.

`IP_TRUSTED` is cleared by the hourly self-test (app/services/ip_selftest.py)
when a spoofed value comes back. While it is False nothing may ban by IP.
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
    # True only after the self-test PASSED (a forged X-Forwarded-For did not
    # come through). "Skipped" (no PUBLIC_API_URL) leaves it False.
    "ip_verified": False,
    "last_selftest": None,
}
_alerted_missing = False


def _peer(request: Any) -> str:
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or ""


def _first_forwarded(request: Any) -> str:
    return (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()


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
        return _legacy(request, prefer_cf)
    value = (request.headers.get(mode) or "").split(",")[0].strip()
    if value:
        return value
    CLIENT_IP_HEALTH["header_missing"] += 1
    _alert_missing_once(mode)
    return _legacy(request, prefer_cf)


def ip_trusted() -> bool:
    return bool(CLIENT_IP_HEALTH["ip_trusted"])


def _reset_for_tests() -> None:
    global _alerted_missing
    _alerted_missing = False
    CLIENT_IP_HEALTH.update(header_missing=0, ip_trusted=True, ip_verified=False, last_selftest=None)
