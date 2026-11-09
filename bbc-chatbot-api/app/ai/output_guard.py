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

Applied where a reply is stored — db.add_message for role="ai", the one place
that counts — and therefore to what the customer receives: a reply in the
response or the greeting is the stored row's text, and a streamed reply goes
out through StreamGuard, which in 'enforce' holds the text back a sentence at
a time so a contact is scrubbed whole before any of it leaves.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from app.flags import flag

logger = logging.getLogger(__name__)

OUTPUT_GUARD_HEALTH = {"scanned": 0, "would_remove": 0, "removed": 0, "alerts": 0, "errors": 0}

# Brand contacts that must survive. Built once from SITE_CONFIGS.
_ALLOWED_PHONES: set[str] = set()
_ALLOWED_DOMAINS: set[str] = set()
_ALLOWED_EMAILS: set[str] = set()
# Accreditation numbers that look like phones but must stay.
_KEEP_NUMBERS = {"14531683", "99910753"}

# What _PHONE_RE takes between the digits of a number. The stream guard
# reads the same set, so a number is never cut where _PHONE_RE would join it.
_PHONE_SEP = r"\s().\-"
_PHONE_RE = re.compile(rf"\+?\d[\d{_PHONE_SEP}]{{7,}}\d")
# The last label must be alphabetic (a TLD): "$1,200.50", "2.5" and
# "12.05.2026" are a price, a decimal and a date, not hosts.
_URL_RE = re.compile(
    r"\b(?:https?://)?(?:www\.)?[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\.[a-z]{2,}\b(?:/[^\s]*)?", re.I
)
# A date, or a range of them ("2026-05-14", "12.05.2026", "2026-05-14 - 2026-05-21"),
# is a run of digits and separators that _PHONE_RE would otherwise take. Each
# part must be a real calendar date, so "12.34.5678" is still a phone.
_ISO_DATE = re.compile(r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})")
_DMY_DATE = re.compile(r"(\d{1,2})[-.](\d{1,2})[-.](\d{2}|\d{4})")
_RANGE_SPLIT = re.compile(r"\s*[-–]\s+|\s+[-–]\s*")


def _is_date(part: str) -> bool:
    m = _ISO_DATE.fullmatch(part)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31
    m = _DMY_DATE.fullmatch(part)
    if m:
        a, b, y = (int(g) for g in m.groups())
        if len(m.group(3)) == 4 and not 1900 <= y <= 2100:
            return False
        # day-month or month-day: one of the two must be a month
        return 1 <= a <= 31 and 1 <= b <= 31 and (a <= 12 or b <= 12)
    return False


def _is_dates(s: str) -> bool:
    parts = [p for p in _RANGE_SPLIT.split(s.strip()) if p]
    return bool(parts) and all(_is_date(p) for p in parts)


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
            if pattern is _PHONE_RE and _is_dates(s):
                return s
            removed.append(s.strip())
            return "[removed]"
        return _repl

    out = _EMAIL_RE.sub(_sub(_EMAIL_RE, _email_ok), text)
    out = _URL_RE.sub(_sub(_URL_RE, _url_ok), out)
    out = _PHONE_RE.sub(_sub(_PHONE_RE, _phone_ok), out)
    return out, removed


_ALERT_COOLDOWN = 3600.0
_last_alert_at = 0.0


def own_contacts(phone: Optional[str], email: Optional[str]) -> set[str]:
    """The client's own phone digits and e-mail, which a reply may repeat back."""
    out = set()
    if phone:
        out.add(_digits(str(phone)))
    if email:
        out.add(str(email).strip().lower())
    return {x for x in out if x}


async def _visitor_contact(conversation_id: Optional[str]) -> set[str]:
    """The conversation's own phone digits and email: the bot repeating the
    client's contact back in a summary is not a leak (review fix)."""
    if not conversation_id:
        return set()
    try:
        from app.db.supabase import get_conversation_simple

        conv = await get_conversation_simple(conversation_id) or {}
    except Exception as e:
        logger.warning(f"[output-guard] contact lookup failed: {type(e).__name__}: {e}")
        return set()
    return own_contacts(conv.get("visitor_phone"), conv.get("visitor_email"))


def _is_own(snippet: str, own: set[str]) -> bool:
    if not own:
        return False
    if "@" in snippet:
        return snippet.strip().lower() in own
    # Digits are compared for a phone only: "scam9.com" carries a 9, and a
    # client's phone ending in 9 must not let that link through as theirs.
    if _kind(snippet) != "phone":
        return False
    d = _digits(snippet)
    return bool(d) and any(d == o or o.endswith(d) or d.endswith(o) for o in own if o.isdigit())


def _alert_later(mode: str, kinds: dict) -> None:
    """Off the chat path, at most once an hour, counts only — never the
    removed values (they are customer contacts)."""
    global _last_alert_at
    import asyncio
    import time as _time

    now = _time.monotonic()
    if now - _last_alert_at < _ALERT_COOLDOWN:
        return
    _last_alert_at = now
    OUTPUT_GUARD_HEALTH["alerts"] += 1

    async def _send() -> None:
        try:
            from app.services.email import send_ops_alert_email

            await send_ops_alert_email(
                "Output guard found a non-brand contact in a bot reply",
                f"mode={mode} kinds={kinds} total_so_far={OUTPUT_GUARD_HEALTH['would_remove']} "
                "(values are not included; see /health/detail.output_guard)",
            )
        except Exception as e:  # noqa: silent — an alert must never fail anything
            logger.warning(f"[output-guard] alert failed: {type(e).__name__}")

    try:
        task = asyncio.get_running_loop().create_task(_send())
        _ALERT_TASKS.add(task)
        task.add_done_callback(_ALERT_TASKS.discard)
    except RuntimeError as e:  # noqa: silent — no running loop (a sync caller): skip the alert, the counter moved
        logger.debug(f"[output-guard] no loop for the alert: {type(e).__name__}")


_ALERT_TASKS: set = set()


def _kind(snippet: str) -> str:
    if "@" in snippet:
        return "email"
    if re.match(r"^(https?:|www\.)", snippet, re.I) or _URL_RE.fullmatch(snippet):
        return "url"
    return "phone"


async def guard_ai_text(text: Optional[str], conversation_id: Optional[str] = None) -> Optional[str]:
    """Scrub a stored AI reply. In 'log' mode the text is returned unchanged.

    The conversation's own contact is never a finding. Logs and alerts carry
    counts and kinds, never the values (rule 7: no PII at INFO or above)."""
    if not text:
        return text
    mode = _mode()
    OUTPUT_GUARD_HEALTH["scanned"] += 1
    clean, removed = _scrub(text)
    if not removed:
        return text
    own = await _visitor_contact(conversation_id)
    foreign = [r for r in removed if not _is_own(r, own)]
    if not foreign:
        return text
    kinds: dict = {}
    for r in foreign:
        kinds[_kind(r)] = kinds.get(_kind(r), 0) + 1
    OUTPUT_GUARD_HEALTH["would_remove"] += len(foreign)
    logger.warning(f"[output-guard] {mode}: {len(foreign)} non-brand contact(s) {kinds} conv={conversation_id}")
    _alert_later(mode, kinds)
    if mode == "enforce":
        OUTPUT_GUARD_HEALTH["removed"] += len(foreign)
        if len(foreign) == len(removed):
            return clean
        # Keep the client's own contact; scrub only the foreign ones.
        return _without_foreign(text, own)
    return text


def _mode() -> str:
    return str(flag("OUTPUT_GUARD_MODE", "log")).strip().lower()


def _without_foreign(text: str, own: set[str]) -> str:
    """The text with every non-brand contact removed, the client's own kept."""
    _, removed = _scrub(text)
    out = text
    for r in removed:
        if not _is_own(r, own):
            out = out.replace(r, "[removed]")
    return out


# Stored and sent in 'enforce' when even the plain scrub breaks: nothing of a
# reply that could not be checked goes out.
WITHHELD_REPLY = "Sorry, I couldn't send that reply. A consultant will follow up with the details."


def guard_failed(text: str, err: BaseException) -> str:
    """The guard itself broke on a reply about to be stored (the caller logs
    `err`): a message is still stored — add_message never loses one — and the
    failure counted. In 'enforce' every contact that is not the brand's goes,
    the client's own too (fail closed), and if that scrub breaks as well the
    reply is withheld whole; in 'log' the text is kept as it would have been."""
    OUTPUT_GUARD_HEALTH["errors"] += 1
    if _mode() != "enforce":
        return text
    try:
        return _scrub(text)[0]
    except Exception as e:
        logger.error(f"[output-guard] scrub failed too, reply withheld: {type(e).__name__}")
        return WITHHELD_REPLY


# The end of a sentence or a line. A URL or an e-mail never spans one; a phone
# can ("555. 123. 4567" is one to _PHONE_RE, and so is "555\r\n123\r\n4567"),
# so an end that comes right after a digit, with only what _PHONE_RE takes
# between digits in between, is not a place to cut.
_PIECE_END = re.compile(r"[.!?](?=\s)\s*|\n\s*")
_ENDS_IN_A_NUMBER = re.compile(rf"\d[{_PHONE_SEP}]*\Z")


class StreamGuard:
    """What a streamed reply may send the customer now.

    A chunk that has gone out cannot be taken back, so in 'enforce' the text
    goes out a sentence (or a line) at a time and each piece is scrubbed whole:
    a contact split across chunks is still seen whole. What is held when the
    reply ends never needs sending — the stored reply (guarded, counted once
    in add_message) replaces the streamed text at stream_end. In 'log' every
    chunk goes out as it comes, as before."""

    def __init__(self, own: Optional[set[str]] = None):
        self.enforce = _mode() == "enforce"
        self.own = own or set()
        self._held = ""

    def feed(self, delta: str) -> str:
        if not self.enforce or not delta:
            return delta
        self._held += delta
        cut = 0
        for m in _PIECE_END.finditer(self._held):
            if _ENDS_IN_A_NUMBER.search(self._held, 0, m.start()):
                continue  # a number ends here: it may go on past the break
            cut = m.end()
        if not cut:
            return ""
        piece, self._held = self._held[:cut], self._held[cut:]
        try:
            return _without_foreign(piece, self.own)
        except Exception as e:  # noqa: silent — logged; the piece is withheld, stream_end brings the stored reply
            logger.error(f"[output-guard] stream piece not scrubbed, withheld: {type(e).__name__}")
            return ""

    def flush(self) -> str:
        """What is still held, scrubbed — for a reply that ends without a stored row."""
        piece, self._held = self._held, ""
        if not self.enforce or not piece:
            return piece
        try:
            return _without_foreign(piece, self.own)
        except Exception as e:  # noqa: silent — logged; nothing unscrubbed goes out
            logger.error(f"[output-guard] stream tail not scrubbed, withheld: {type(e).__name__}")
            return ""


def _reset_for_tests() -> None:
    global _last_alert_at
    _last_alert_at = 0.0
    OUTPUT_GUARD_HEALTH.update(scanned=0, would_remove=0, removed=0, alerts=0, errors=0)
