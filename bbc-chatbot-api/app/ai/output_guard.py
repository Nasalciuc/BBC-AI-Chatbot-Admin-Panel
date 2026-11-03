"""Output guard — a bot reply must never hand a customer a contact that is not
the brand's.

A prompt-injected page, utm value or lesson can make the model print an
attacker's phone, link or email. This strips any phone / URL / email from a
stored AI reply that is not one of the brand's own (SITE_CONFIGS), and keeps
the IATA (#14531683) and TRUE (#99910753) accreditation numbers.

OUTPUT_GUARD_MODE (app/flags.py):
  - "log"     : report what it WOULD remove, change nothing (the merge default,
                two days of watching before enforcing);
  - "enforce" : actually remove, set by the owner.

Applied in one place — db.add_message for role="ai" — and, for the streamed
path, through scrub_stream_buffer, which holds back a suspicious tail (digits,
http, www., @) until it is whole so a redaction is never split across chunks.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from app.flags import flag

logger = logging.getLogger(__name__)

OUTPUT_GUARD_HEALTH = {"scanned": 0, "would_remove": 0, "removed": 0, "alerts": 0}

# Brand contacts that must survive. Built once from SITE_CONFIGS.
_ALLOWED_PHONES: set[str] = set()
_ALLOWED_DOMAINS: set[str] = set()
_ALLOWED_EMAILS: set[str] = set()
# Accreditation numbers that look like phones but must stay.
_KEEP_NUMBERS = {"14531683", "99910753"}

_PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{7,}\d")
_URL_RE = re.compile(r"\b(?:https?://)?(?:www\.)?[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)+(?:/[^\s]*)?", re.I)
_EMAIL_RE = re.compile(r"\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b", re.I)


def _load_allowed() -> None:
    if _ALLOWED_PHONES:
        return
    try:
        from app.ai.prompts import SITE_CONFIGS

        for cfg in SITE_CONFIGS.values():
            phone = cfg.get("contact_phone")
            if phone:
                _ALLOWED_PHONES.add(_digits(phone))
            email = cfg.get("contact_email")
            if email:
                _ALLOWED_EMAILS.add(email.lower())
            site = (cfg.get("website") or "").lower().lstrip("/")
            if site:
                _ALLOWED_DOMAINS.add(site.replace("www.", ""))
    except Exception as e:  # noqa: silent — without the configs the guard keeps everything, never crashes
        logger.warning(f"[output-guard] could not load SITE_CONFIGS: {type(e).__name__}: {e}")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _phone_ok(match: str) -> bool:
    d = _digits(match)
    if d in _KEEP_NUMBERS:
        return True
    # A brand phone can appear with or without the country code.
    for allowed in _ALLOWED_PHONES:
        if d == allowed or d == allowed.lstrip("1") or allowed.endswith(d) or d.endswith(allowed):
            return True
    return False


def _url_ok(match: str) -> bool:
    host = re.sub(r"^https?://", "", match, flags=re.I).split("/")[0].lower().replace("www.", "")
    return any(host == d or host.endswith("." + d) for d in _ALLOWED_DOMAINS)


def _email_ok(match: str) -> bool:
    e = match.lower()
    if e in _ALLOWED_EMAILS:
        return True
    domain = e.split("@")[-1]
    return any(domain == d or domain.endswith("." + d) for d in _ALLOWED_DOMAINS)


def _scrub(text: str) -> tuple[str, list[str]]:
    """Return (clean_text, removed_snippets)."""
    _load_allowed()
    removed: list[str] = []

    def _sub(pattern, ok):
        def _repl(m):
            s = m.group(0)
            # keep short digit runs that are not really contacts (e.g. "2 adults")
            if ok(s):
                return s
            if pattern is _PHONE_RE and len(_digits(s)) < 7:
                return s
            removed.append(s.strip())
            return "[removed]"
        return _repl

    out = _EMAIL_RE.sub(_sub(_EMAIL_RE, _email_ok), text)
    out = _URL_RE.sub(_sub(_URL_RE, _url_ok), out)
    out = _PHONE_RE.sub(_sub(_PHONE_RE, _phone_ok), out)
    return out, removed


async def guard_ai_text(text: Optional[str]) -> Optional[str]:
    """Scrub a stored AI reply. In 'log' mode the text is returned unchanged."""
    if not text:
        return text
    mode = str(flag("OUTPUT_GUARD_MODE", "log")).strip().lower()
    OUTPUT_GUARD_HEALTH["scanned"] += 1
    clean, removed = _scrub(text)
    if not removed:
        return text
    OUTPUT_GUARD_HEALTH["would_remove"] += len(removed)
    logger.warning(f"[output-guard] {mode}: would remove {removed}")
    if OUTPUT_GUARD_HEALTH["would_remove"] % 3 == 0:
        OUTPUT_GUARD_HEALTH["alerts"] += 1
        try:
            from app.services.email import send_ops_alert_email

            await send_ops_alert_email(
                "Output guard removed a non-brand contact",
                f"mode={mode} removed={removed}",
            )
        except Exception as e:  # noqa: silent — an alert must never fail the reply
            logger.warning(f"[output-guard] alert failed: {type(e).__name__}")
    if mode == "enforce":
        OUTPUT_GUARD_HEALTH["removed"] += len(removed)
        return clean
    return text


_TAIL_RE = re.compile(r"(?:[\d@]|https?:|www\.|\+)[\w@.:/\-]*$", re.I)


def scrub_stream_buffer(buffer: str) -> tuple[str, str]:
    """Split a streaming buffer into (safe_to_emit, held_tail).

    Holds back a trailing run that might be the start of a contact so a
    redaction is never split across chunks. The caller scrubs safe_to_emit and
    keeps held_tail for the next chunk; the final flush scrubs the remainder.
    """
    m = _TAIL_RE.search(buffer)
    if not m:
        return buffer, ""
    return buffer[: m.start()], buffer[m.start():]


def _reset_for_tests() -> None:
    OUTPUT_GUARD_HEALTH.update(scanned=0, would_remove=0, removed=0, alerts=0)
