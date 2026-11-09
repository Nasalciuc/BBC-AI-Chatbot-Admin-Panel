"""Does the edge let a client forge its own IP? Ask, don't assume.

Railway's own forum gives two contradictory answers about which header is
authoritative. So once an hour this calls our public /health/ip-echo through
PUBLIC_API_URL with a TEST-NET-3 address (203.0.113.7, RFC 5737, never a real
client) placed in both X-Forwarded-For and the configured header. If that
address comes back as the client IP, the edge trusted a client-supplied value:
IP_TRUSTED is cleared, ops is alerted, and nothing may ban by IP (PR-11) until
it is set again.
"""

from __future__ import annotations

import logging

from config.settings import settings
from app.security.client_ip import CLIENT_IP_HEALTH

logger = logging.getLogger(__name__)

_SPOOF = "203.0.113.7"  # TEST-NET-3
_alerted = False


async def run_ip_selftest() -> dict:
    """Scheduler job. Returns a small dict for the heartbeat canary."""
    global _alerted
    import time as _time

    base = (getattr(settings, "public_api_url", "") or "").rstrip("/")
    secret = (settings.cron_secret or "").strip()
    CLIENT_IP_HEALTH["last_selftest"] = _time.time()
    if not base or not secret:
        CLIENT_IP_HEALTH["ip_verified"] = None  # never run: unverified, not safe
        return {"skipped": "PUBLIC_API_URL or CRON_SECRET not set"}

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base}/health/ip-echo",
                headers={
                    "Authorization": f"Bearer {secret}",
                    "X-Forwarded-For": _SPOOF,
                    "X-Real-IP": _SPOOF,
                },
            )
        # Only a real echo is evidence. A 401/403 (a rotated CRON_SECRET) has
        # no address in it, and "" != the forged one read as "edge safe":
        # trusted and verified on nothing, clearing a spoof found earlier.
        resp.raise_for_status()
        body = resp.json() or {}
        # The configured source alone, never the fallback (see source_ip):
        # otherwise a detected edge reads as fixed on the next run.
        seen = body.get("source_ip") if isinstance(body, dict) else None
        if not isinstance(seen, str) or not seen:
            raise ValueError("ip-echo answered without a source_ip")
    except Exception as e:
        CLIENT_IP_HEALTH["ip_verified"] = False  # the test failed: nothing proven
        logger.warning(f"[ip-selftest] could not reach ip-echo: {type(e).__name__}: {e}")
        return {"error": type(e).__name__}

    spoofable = seen == _SPOOF
    CLIENT_IP_HEALTH["ip_trusted"] = not spoofable
    # Positive proof (review fix): only a passed self-test lets the bans that
    # never wait land on an IP — a skipped test is not proof.
    CLIENT_IP_HEALTH["ip_verified"] = not spoofable
    if spoofable and not _alerted:
        _alerted = True
        from app.services.email import send_ops_alert_email

        await send_ops_alert_email(
            "Client IP is spoofable",
            f"A forged {_SPOOF} came back from /health/ip-echo as the client IP. "
            "IP_TRUSTED is now false; IP bans are suspended until the edge/header is fixed.",
        )
    elif not spoofable:
        _alerted = False
    return {"spoofable": spoofable, "seen": seen}
