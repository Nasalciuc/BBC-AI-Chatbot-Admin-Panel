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

Prices (owner, 18 Nov 2026: only a consultant ever says a fare): a sentence of an AI
reply that names an amount of money is dropped whole, always — not only in 'enforce' —
and the reply ends with PRICE_LINE. A streamed sentence that holds a digit or a currency
sign is held until it ends, so an amount never goes out before the check.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from app.flags import flag

logger = logging.getLogger(__name__)

OUTPUT_GUARD_HEALTH = {"scanned": 0, "would_remove": 0, "removed": 0, "alerts": 0, "errors": 0,
                       "prices_removed": 0}

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


# ── Prices: only a consultant ever says a fare ────────────────────────────────
# An amount of money in any currency, the sign or the word on either side: "$3,200", "€2.5k", "US$ 900",
# "3.200 €", "3,200 dollars", "900 US dollars", "USD 4,500", "R$ 3.200", "2.000 dólares".
# A percentage ("30-60%"), a date, a count of people or an accreditation number is no amount; neither is
# a weight ("23 kg", "70 pounds" of baggage: "pounds" alone is a weight, "pounds sterling" a currency).
# The currencies of the countries clients write from (app.services.phone_country), by code and by name
# (CodeRabbit #270: "CHF 3,200", "¥320,000", "3,200 pesos"). Codes and names that are also everyday words
# stay out ("try", "mad", "pen", "won", "real", "rand"): a sentence is never dropped for one.
_CUR_SIGNS = "$€£¥₹₩₺₽₪₱฿"
_CUR_WORD = (r"(?:usd|us\$|r\$|eur|gbp|cad|aud|(?:u\.?s\.?\s?)?dollars?|d[óo]lares?|euros?"
             r"|pounds?\s+sterling|bucks"
             r"|chf|(?:swiss\s+)?francs?|jpy|yen|cny|rmb|yuan|inr|rupees?|mxn|pesos?|aed|dirhams?|sgd|hkd|nzd"
             r"|zar|brl|reais|sek|nok|dkk|kronor|kroner|pln|z[łl]oty|czk|huf|forints?|sar|riyals?|qar|kwd|dinars?"
             r"|egp|thb|baht|krw|twd|ars|clp|cop|uyu|liras?|rubles?|roubles?|shekels?|kr|zł|rs\.?)")
_AMOUNT = r"\d(?:[\d,.]*\d)?(?:\s?[km]\b)?"  # ends on a digit: a full stop after it is the sentence's
_MONEY_RE = re.compile(
    rf"(?:[{_CUR_SIGNS}]\s?{_AMOUNT})|(?:\b{_AMOUNT}\s?[{_CUR_SIGNS}](?!\w))"
    rf"|(?:\b{_AMOUNT}\s?{_CUR_WORD}(?![a-z]))|(?:(?<![a-z]){_CUR_WORD}\s?{_AMOUNT})", re.I
)
# What starts holding a streamed sentence: a digit or a currency sign/word could begin an amount.
_MONEY_HINT = re.compile(rf"[{_CUR_SIGNS}\d]|(?<![a-z]){_CUR_WORD}(?![a-z])", re.I)
# A chunk that ends on the first letters of a currency word ("US" of "USD") could still become one:
# the stream keeps them until the next chunk says (StreamGuard._watch_prices).
_CUR_WORDS = ("usd", "us$", "r$", "eur", "euro", "euros", "gbp", "cad", "aud", "u.s.", "dollar", "dollars",
              "dólar", "dólares", "dolar", "dolares", "bucks", "pounds",
              "chf", "swiss", "franc", "francs", "jpy", "yen", "cny", "rmb", "yuan", "inr", "rupee", "rupees",
              "mxn", "peso", "pesos", "aed", "dirham", "dirhams", "sgd", "hkd", "nzd", "zar", "brl", "reais",
              "sek", "nok", "dkk", "kronor", "kroner", "pln", "zloty", "złoty", "czk", "huf", "forint", "forints",
              "sar", "riyal", "riyals", "qar", "kwd", "dinar", "dinars", "egp", "thb", "baht", "krw", "twd",
              "ars", "clp", "cop", "uyu", "lira", "liras", "ruble", "rubles", "rouble", "roubles", "shekel",
              "shekels", "kr", "zł", "rs", "rs.")
_CUR_PREFIXES = frozenset(w[:i] for w in _CUR_WORDS for i in range(1, len(w) + 1))
_TRAILING_WORD = re.compile(r"(?<![^\W\d_])[^\W\d_][\w.$]{0,7}\Z")
PRICE_LINE = "Exact fares come from your consultant — they check live availability for your dates."


def _pieces(text: str) -> list[str]:
    """The text cut into sentences (or lines), separators kept, so joining gives the text back."""
    out, start = [], 0
    for m in _PIECE_END.finditer(text):
        out.append(text[start:m.end()])
        start = m.end()
    if start < len(text):
        out.append(text[start:])
    return out


def has_price(text: Optional[str]) -> bool:
    return bool(text) and bool(_MONEY_RE.search(text))


def _drop_priced(text: str) -> tuple[str, int]:
    """The pieces (sentences, lines) of `text` that touch no amount, joined, and how many went. An amount
    is found in the whole text first: one that crosses a line break ("3,200\nUSD") takes both pieces."""
    spans = [m.span() for m in _MONEY_RE.finditer(text)]
    if not spans:
        return text, 0
    kept, dropped, pos = [], 0, 0
    for piece in _pieces(text):
        start, end = pos, pos + len(piece)
        pos = end
        if any(s < end and e > start for s, e in spans):
            dropped += 1
        else:
            kept.append(piece)
    return "".join(kept), dropped


def scrub_prices(text: Optional[str]) -> tuple[Optional[str], int]:
    """The reply without its sentences that name an amount, ending with PRICE_LINE when one went;
    and how many went. Text without an amount comes back unchanged."""
    if not text or not _MONEY_RE.search(text):
        return text, 0
    out, dropped = _drop_priced(text)
    out = out.strip()
    if PRICE_LINE not in out:
        out = f"{out} {PRICE_LINE}".strip() if out else PRICE_LINE
    return out, dropped


# A range is one amount even when only one end names the currency: "$2,800–$4,500", "$2,800-4,500",
# "2,800–4,500 USD". "7-8 hours" names none: no amount. The bare end looks like money (three digits or
# more, groups of thousands, or "2.5k"): a day beside a fare stays ("Departure: Dec 10 — $500";
# CodeRabbit #270).
_RANGE_SEP = r"\s*(?:-|–|—|to)\s*"
_RANGE_END = r"(?:\d{1,3}(?:[,.]\d{3})+(?:[.,]\d{1,2})?|\d{3,}(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?\s?[km]\b)(?![\d,.]*\d)"
_MONEY_RANGE_RE = re.compile(
    rf"(?:(?<![\d,.]){_RANGE_END}{_RANGE_SEP}(?=(?:{_MONEY_RE.pattern})))?(?:{_MONEY_RE.pattern})"
    rf"(?:{_RANGE_SEP}(?:{_MONEY_RE.pattern}|{_RANGE_END}))?", re.I
)
KB_AMOUNT_NOTE = "(fares: quoted only by a consultant)"


# The words an amount leaves behind when it is taken out of a short label: "London from $7,345" → "London".
_LEFT_BY_AN_AMOUNT = re.compile(
    r"[\s,;:–—-]*(?:\b(?:from|starting\s+(?:at|from)|starts\s+at|only|just|for|at|desde|dès|a\s+partir\s+de"
    r"|à\s+partir\s+de|por|pour|ab)\b[\s,;:–—-]*)*$", re.I)


def without_amounts(text: Optional[str]) -> str:
    """A short label (a route card's place or airlines) with its amounts taken out, and the "from" one
    leaves behind: the card shows the place, never a fare (CodeRabbit #270)."""
    if not text:
        return ""
    out = _MONEY_RANGE_RE.sub(" ", text)
    if out == text:
        return text.strip()
    return _LEFT_BY_AN_AMOUNT.sub("", re.sub(r"\s{2,}", " ", out)).strip(" ,;:–—-")


def strip_amounts(text: Optional[str]) -> Optional[str]:
    """Knowledge-base text with every amount (and range of amounts) replaced by KB_AMOUNT_NOTE,
    before it reaches the model: what the model never reads it never repeats."""
    if not text:
        return text
    return _MONEY_RANGE_RE.sub(KB_AMOUNT_NOTE, text)


def _price_checked(text: str, conversation_id: Optional[str]) -> str:
    clean, dropped = scrub_prices(text)
    if dropped:
        OUTPUT_GUARD_HEALTH["prices_removed"] += dropped
        # Counts only: the amount is never logged.
        logger.warning(f"[price-guard] dropped {dropped} sentence(s) naming an amount conv={conversation_id}")
    return clean or text


async def guard_ai_text(text: Optional[str], conversation_id: Optional[str] = None) -> Optional[str]:
    """Scrub a stored AI reply. In 'log' mode the text is returned unchanged.

    The conversation's own contact is never a finding. Logs and alerts carry
    counts and kinds, never the values (rule 7: no PII at INFO or above)."""
    if not text:
        return text
    text = _price_checked(text, conversation_id)
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
    in add_message) replaces the streamed text at stream_end.

    Prices, in every mode: a sentence goes out as it comes until it shows a
    digit or a currency sign; from there it is held to its end and dropped if
    it names an amount (the stored reply, ending with PRICE_LINE, replaces the
    stream at stream_end). A sentence without a digit streams as before."""

    def __init__(self, own: Optional[set[str]] = None):
        self.enforce = _mode() == "enforce"
        self.own = own or set()
        self._held = ""
        self._holding = False   # 'log' mode: the current sentence showed a digit or a currency sign

    def feed(self, delta: str) -> str:
        if not delta:
            return delta
        if not self.enforce:
            return self._watch_prices(delta)
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
            return _drop_priced(_without_foreign(piece, self.own))[0]
        except Exception as e:  # noqa: silent — logged; the piece is withheld, stream_end brings the stored reply
            logger.error(f"[output-guard] stream piece not scrubbed, withheld: {type(e).__name__}")
            return ""

    def _watch_prices(self, delta: str) -> str:
        """'log' mode: stream as it comes, but hold a sentence from its first digit or currency
        sign to its end, and drop that held part if it names an amount. A chunk that ends on the first
        letters of a currency word ("US" of "USD") keeps them until the next chunk says."""
        self._held += delta
        out = []
        while self._held:
            end = _PIECE_END.search(self._held)
            if not self._holding:
                hint = _MONEY_HINT.search(self._held)
                if not hint or (end and hint.start() >= end.end()):
                    # no sign of an amount in the current sentence: send up to its end (or all of it,
                    # but the first letters of a currency word the next chunk may finish)
                    if end:
                        cut = end.end()
                    else:
                        tail = _TRAILING_WORD.search(self._held)
                        cut = tail.start() if tail and tail.group(0).lower() in _CUR_PREFIXES else len(self._held)
                    out.append(self._held[:cut])
                    self._held = self._held[cut:]
                    if not end:
                        break
                    continue
                out.append(self._held[:hint.start()])
                self._held = self._held[hint.start():]
                self._holding = True
                continue
            end = next((m for m in _PIECE_END.finditer(self._held)
                        if not (m.group(0).startswith("\n") and _ENDS_IN_A_NUMBER.search(self._held, 0, m.start()))),
                       None)
            if not end:
                break  # keep holding until the sentence ends (a line break right after a number is not its end)
            piece, self._held = self._held[:end.end()], self._held[end.end():]
            self._holding = False
            if has_price(piece):
                OUTPUT_GUARD_HEALTH["prices_removed"] += 1
                continue
            out.append(piece)
        return "".join(out)

    def flush(self) -> str:
        """What is still held, scrubbed — for a reply that ends without a stored row."""
        piece, self._held = self._held, ""
        self._holding = False
        if not piece:
            return piece
        if not self.enforce:
            return "" if has_price(piece) else piece
        try:
            return _drop_priced(_without_foreign(piece, self.own))[0]
        except Exception as e:  # noqa: silent — logged; nothing unscrubbed goes out
            logger.error(f"[output-guard] stream tail not scrubbed, withheld: {type(e).__name__}")
            return ""


def _reset_for_tests() -> None:
    global _last_alert_at
    _last_alert_at = 0.0
    OUTPUT_GUARD_HEALTH.update(scanned=0, would_remove=0, removed=0, alerts=0, errors=0)
