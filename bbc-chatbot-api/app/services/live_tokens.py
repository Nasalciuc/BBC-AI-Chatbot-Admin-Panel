"""The tokens of "On the site now" (bbc-live, contracts/live.md). The API is the only one that issues them.

  l1.<payload>.<sig>   an agent, shown to bbc-live by the panel: {"u": user id, "r": role, "t": tunnel
                       scope, "iat", "exp"}, AGENT_TTL. The heartbeat hands a fresh one every beat.
  t1.<payload>.<sig>   a visitor's tap on an agent's hello, shown to /chat/init by the widget: {"v": visitor
                       id, "a": agent id, "h": hello id, "iat", "exp"}, TAP_TTL. Only this API reads it.

payload = base64url, no padding, of the JSON with sorted keys and no spaces; sig = hex HMAC-SHA256 of
"<prefix>.<payload>" with LIVE_TOKEN_SECRET. contracts/live_tokens.json holds vectors both this module and
bbc-live's Go tests check. The widget shows bbc-live its own visitor token (app.security.visitor_token).

No name, e-mail or phone goes into a token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Optional

AGENT_TTL = 60
TAP_TTL = 900


def _secret() -> bytes:
    from config.settings import settings
    return (getattr(settings, "live_token_secret", "") or "").encode()


def enabled() -> bool:
    from config.settings import settings
    return bool(_secret()) and bool((getattr(settings, "live_service_url", "") or "").strip())


def _sign(body: str, secret: bytes) -> str:
    return hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()


def encode(prefix: str, payload: dict, secret: bytes) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    body = prefix + "." + base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{body}.{_sign(body, secret)}"


def decode(token: Optional[str], prefix: str, secret: bytes, *, now: Optional[int] = None) -> tuple[Optional[dict], str]:
    """(payload, "ok"), or (None, missing | invalid | expired | disabled)."""
    if not secret:
        return None, "disabled"
    if not token:
        return None, "missing"
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != prefix:
        return None, "invalid"
    body = parts[0] + "." + parts[1]
    # Bytes, as in visitor_token: the signature is untrusted input and may not be ASCII.
    if not hmac.compare_digest(parts[2].encode("utf-8", "surrogatepass"), _sign(body, secret).encode()):
        return None, "invalid"
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, TypeError):  # noqa: silent — a signed token whose payload doesn't decode is malformed, reported as invalid
        return None, "invalid"
    if not isinstance(payload, dict) or not isinstance(payload.get("exp"), int):
        return None, "invalid"
    if int(now if now is not None else time.time()) > payload["exp"]:
        return None, "expired"
    return payload, "ok"


def agent_token(user: dict, *, now: Optional[int] = None) -> Optional[str]:
    """The panel's pass to bbc-live for this user, or None (no secret, no id)."""
    secret = _secret()
    uid, role = user.get("id"), user.get("role")
    if not secret or not uid or not role:
        return None
    iat = int(now if now is not None else time.time())
    return encode("l1", {"u": str(uid), "r": str(role), "t": str(user.get("tunnel_scope") or role),
                         "iat": iat, "exp": iat + AGENT_TTL}, secret)


def tap_token(visitor_id: str, agent_id: str, hello_id: str, *, now: Optional[int] = None) -> Optional[str]:
    secret = _secret()
    if not secret:
        return None
    iat = int(now if now is not None else time.time())
    return encode("t1", {"v": visitor_id, "a": agent_id, "h": hello_id, "iat": iat, "exp": iat + TAP_TTL}, secret)


def read_tap(token: Optional[str], *, now: Optional[int] = None) -> tuple[Optional[dict], str]:
    payload, reason = decode(token, "t1", _secret(), now=now)
    if payload is not None and not all(isinstance(payload.get(k), str) and payload[k] for k in ("v", "a", "h")):
        return None, "invalid"
    return payload, reason
