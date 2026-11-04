"""Progressive backoff on failed logins, keyed on email + IP.

Keyed on the pair, not the email alone: a single-email counter lets anyone
lock a staff member out of their own account by guessing against a public
address. The pair stops a password-spray from one address while a real user,
on a different address, is never blocked by someone else's failures.

Redis when REDIS_URL is set (survives a restart, shared by any future
replica), else an in-process dict (one worker today, ADR-5). Fail-open: a
Redis error never blocks a login.
"""

from __future__ import annotations

import logging
import time
from threading import Lock
from typing import Optional

from app.flags import flag

logger = logging.getLogger(__name__)

# attempt count -> seconds the next attempt must wait
_BACKOFF = {5: 60, 6: 120, 7: 240, 8: 480, 9: 900}
# The same ladder on the EMAIL alone, from 4x the attempts. The pair key is
# only as good as the IP, and the IP is client-controlled until the edge is
# proven (client_ip.py): with a fresh X-Forwarded-For per attempt every pair
# is new. This ceiling holds whatever the IP says, and is high enough that a
# stranger guessing at a staff address costs that person little.
_EMAIL_FACTOR = 4
_MAX_WAIT = 900
_WINDOW = 3600  # attempts older than this are forgotten
_ALERT_AT = 10  # failures within the window → one ops alert

LOGIN_GUARD_HEALTH = {"throttled": 0, "email_throttled": 0, "alerts": 0, "redis_errors": 0}

_mem: dict[str, list[float]] = {}
_mem_lock = Lock()


def _key(email: str, ip: str) -> str:
    # While the self-test has seen the edge accept a forged IP, a new
    # X-Forwarded-For per attempt would be a new pair per attempt and no
    # backoff at all. The email alone is then the key: a lockout risk the
    # owner is alerted to, instead of an unlimited password guess.
    from app.security.client_ip import ip_trusted

    if not ip_trusted():
        return f"login_fail:{(email or '').strip().lower()}|*"
    return f"login_fail:{(email or '').strip().lower()}|{ip or 'unknown'}"


def _email_key(email: str) -> str:
    return f"login_fail_email:{(email or '').strip().lower()}"


def _redis():
    try:
        from app.services.response_cache import _get_redis

        return _get_redis()
    except Exception as e:
        LOGIN_GUARD_HEALTH["redis_errors"] += 1
        logger.warning(f"[login-guard] redis lookup failed, using memory: {type(e).__name__}: {e}")
        return None


def _recent(key: str, now: float) -> list[float]:
    r = _redis()
    if r is not None:
        try:
            raw = r.zrangebyscore(key, now - _WINDOW, now)
            return [float(x) for x in raw]
        except Exception as e:
            LOGIN_GUARD_HEALTH["redis_errors"] += 1
            logger.warning(f"[login-guard] redis read failed, using memory: {type(e).__name__}: {e}")
    with _mem_lock:
        items = [t for t in _mem.get(key, []) if t >= now - _WINDOW]
        _mem[key] = items
        return list(items)


def _wait_for(n_recent: int, factor: int = 1) -> int:
    """Seconds the next attempt must wait, given how many failures are on record."""
    n = n_recent // factor
    if n < 5:
        return 0
    return _BACKOFF.get(n, _MAX_WAIT)


def _left(key: str, now: float, factor: int) -> int:
    recent = sorted(_recent(key, now))
    wait = _wait_for(len(recent), factor)
    if wait and recent:
        return max(0, int(recent[-1] + wait - now))
    return 0


def retry_after(email: str, ip: str) -> int:
    """0 if a login may proceed now, else the seconds left to wait.

    The longer of the pair's wait and the email-wide one."""
    if not flag("LOGIN_BACKOFF_ENABLED", True):
        return 0
    now = time.time()
    pair = _left(_key(email, ip), now, 1)
    wide = _left(_email_key(email), now, _EMAIL_FACTOR)
    left = max(pair, wide)
    if left > 0:
        LOGIN_GUARD_HEALTH["throttled"] += 1
        if wide > pair:
            LOGIN_GUARD_HEALTH["email_throttled"] += 1
        return left
    return 0


async def record_failure(email: str, ip: str) -> None:
    """Called after a rejected password. Alerts once at the 10th failure in the window."""
    now = time.time()
    _record(_email_key(email), now)  # the email-wide ceiling, whatever the IP
    count = _record(_key(email, ip), now)

    if count == _ALERT_AT:
        LOGIN_GUARD_HEALTH["alerts"] += 1
        from app.services.email import send_ops_alert_email

        await send_ops_alert_email(
            "Repeated failed logins",
            f"{_ALERT_AT} failed logins within an hour for {email or '?'} from {ip or '?'}.",
        )


def _record(key: str, now: float) -> int:
    """Add one failure under key; the count within the window."""
    r = _redis()
    if r is not None:
        try:
            r.zadd(key, {f"{now}": now})
            r.zremrangebyscore(key, 0, now - _WINDOW)
            r.expire(key, _WINDOW)
            count = r.zcount(key, now - _WINDOW, now)
        except Exception as e:
            LOGIN_GUARD_HEALTH["redis_errors"] += 1
            logger.warning(f"[login-guard] redis write failed, using memory: {type(e).__name__}: {e}")
            count = _record_memory(key, now)
    else:
        count = _record_memory(key, now)
    return int(count)


def _record_memory(key: str, now: float) -> int:
    with _mem_lock:
        items = [t for t in _mem.get(key, []) if t >= now - _WINDOW]
        items.append(now)
        _mem[key] = items
        return len(items)


def clear(email: str, ip: str) -> None:
    """A correct password clears the pair's counter and the email-wide one."""
    for key in (_key(email, ip), _email_key(email)):
        r = _redis()
        if r is not None:
            try:
                r.delete(key)
            except Exception as e:
                LOGIN_GUARD_HEALTH["redis_errors"] += 1
                logger.warning(f"[login-guard] redis clear failed: {type(e).__name__}: {e}")
        with _mem_lock:
            _mem.pop(key, None)


def _reset_for_tests() -> None:
    with _mem_lock:
        _mem.clear()
    LOGIN_GUARD_HEALTH.update(throttled=0, email_throttled=0, alerts=0, redis_errors=0)
