"""Follow-ups: what an agent promised a client in the chat, or what a client
asked for. The rules behind Tasks (design canvas "Tasks · the new feature to
build", 8 Nov 2026).

Pure. Fixed phrases, no AI: the same message always gives the same task, and
an owner can read (and change) every phrase that makes one. Real phrasing,
from 30 days of agent messages (8 Nov 2026): "I will send you the price quotes
via email", "I'll email some options to you in 30-60mins", "Give me half an
hour and I will get back to you", "is it okay if I will call you on your
number?" (a question: not a promise).

Times: the panel works in one business zone (New York by default). A time an
agent writes ("by 5") is read in the client's zone when we know it (the
departure city, airport_tz.py), else in the business zone; relative times
("within the hour") need no zone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional

# ── Rules ────────────────────────────────────────────────────────────────────

DEFAULT_PHRASES = ["I'll send", "I'll e-mail", "I'll call", "we'll call", "get back to you", "follow up"]

DEFAULT_RULES: dict[str, Any] = {
    "catch_promises": True,
    # When the message says no time: "1h" | "2h" | "end_of_day" | "next_morning".
    "default_due": "2h",
    "time_zone": "America/New_York",
    "day_start": "08:00",
    "day_end": "23:00",
    # Open tasks of someone who goes Not ready: "unowned" | "ready" (whoever is Ready).
    "leave_shift": "unowned",
    "phrases": list(DEFAULT_PHRASES),
}
DEFAULT_DUES = ("1h", "2h", "end_of_day", "next_morning")
LEAVE_SHIFT = ("unowned", "ready")
MORNING = time(10, 0)
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def rules_of(raw: Any) -> dict[str, Any]:
    """The stored rules over the defaults; anything unreadable keeps its default."""
    out = {**DEFAULT_RULES, "phrases": list(DEFAULT_PHRASES)}
    if not isinstance(raw, dict):
        return out
    if isinstance(raw.get("catch_promises"), bool):
        out["catch_promises"] = raw["catch_promises"]
    if raw.get("default_due") in DEFAULT_DUES:
        out["default_due"] = raw["default_due"]
    if raw.get("leave_shift") in LEAVE_SHIFT:
        out["leave_shift"] = raw["leave_shift"]
    if isinstance(raw.get("time_zone"), str) and zone(raw["time_zone"]) is not None:
        out["time_zone"] = raw["time_zone"]
    for k in ("day_start", "day_end"):
        if isinstance(raw.get(k), str) and _HHMM.match(raw[k]):
            out[k] = raw[k]
    if isinstance(raw.get("phrases"), list):
        phrases = [p.strip() for p in raw["phrases"] if isinstance(p, str) and 2 < len(p.strip()) <= 60]
        out["phrases"] = list(dict.fromkeys(phrases))
    return out


def zone(name: Optional[str]):
    """A ZoneInfo, or None when the name is unknown or the tz database is missing."""
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: silent — an unknown zone name means "no zone": the caller shows only the panel's time, nothing is written
        return None


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


# ── Text ─────────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Lower case, one kind of apostrophe, "I will" → "i'll", "e-mail" → "email"."""
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"\be-mail", "email", t)
    t = re.sub(r"\bi\s+will\b", "i'll", t)
    t = re.sub(r"\bwe\s+will\b", "we'll", t)
    t = re.sub(r"\bi\s*'\s*ll\b", "i'll", t)
    t = re.sub(r"\bwe\s*'\s*ll\b", "we'll", t)
    return re.sub(r"\s+", " ", t).strip()


def _phrase_re(phrase: str) -> re.Pattern:
    words = normalize(phrase).split(" ")
    return re.compile(r"(?<![\w'])" + r"\s+".join(re.escape(w) for w in words) + r"(?![\w'])")


_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")
_CALL_WORDS = re.compile(r"\b(call|phone|ring)\b")
_EMAIL_WORDS = re.compile(r"\b(email|send|quotes?|options|fares?|itinerar(y|ies)|prices?)\b")


@dataclass(frozen=True)
class Catch:
    kind: str           # "call" | "email" | "todo"
    phrase: str         # the rule's phrase that matched, as the owner wrote it
    sentence: str       # the sentence from the message, as the agent wrote it


def catch_promise(text: str, phrases: list[str]) -> Optional[Catch]:
    """The first sentence with a promise phrase that isn't a question, or None."""
    compiled = [(p, _phrase_re(p)) for p in phrases if p and p.strip()]
    for m in _SENTENCE.finditer(text or ""):
        sentence = m.group(0).strip()
        if not sentence or sentence.endswith("?"):
            continue
        norm = normalize(sentence)
        for phrase, rx in compiled:
            if rx.search(norm):
                return Catch(kind=kind_of(normalize(phrase), norm), phrase=phrase, sentence=sentence)
    return None


def kind_of(norm_phrase: str, norm_sentence: str) -> str:
    """The phrase says it when it can ("I'll call", "I'll e-mail"); "get back to
    you" and "follow up" take it from the rest of the sentence."""
    for text in (norm_phrase, norm_sentence):
        if _CALL_WORDS.search(text):
            return "call"
        if _EMAIL_WORDS.search(text):
            return "email"
    return "todo"


# ── Titles ───────────────────────────────────────────────────────────────────

_OBJ_STOP = (
    r"within|in|by|at|after|before|today|tonight|tomorrow|shortly|soon|asap|via|over|on|"
    r"to your|to you|with|and|so|once|as soon|right away|later|when|if|for you"
)
_OBJ = re.compile(
    r"\b(?:send|email)\s+(?:you\s+|it\s+to\s+you\s+|them\s+to\s+you\s+)?(?P<obj>.+?)"
    r"(?=\s+(?:" + _OBJ_STOP + r")\b|[,.!;:]|$)"
)
_VAGUE = {"", "you", "it", "them", "that", "this", "those", "these", "both", "all", "everything",
          "something", "email", "an email", "you an email", "an sms", "a message", "the details", "details"}
_PURPOSE = re.compile(r"\bto\s+(?P<p>(?:confirm|discuss|go over|talk about|check|finali[sz]e|book|review|explain)\b[^,.!;:]{0,60})")


def title_for(kind: str, sentence: str, first_name: Optional[str], route: Optional[str], *, client_asked: bool = False) -> str:
    """A short title in the agent's own words: "Send Michael three options for New York → London"."""
    who = (first_name or "").strip() or "the client"
    norm = normalize(sentence)
    if kind == "call":
        if client_asked:
            return f"Call {who} back"
        purpose = _PURPOSE.search(norm)
        return f"Call {who} to {purpose.group('p').strip()}" if purpose else f"Call {who} back"
    if kind == "email":
        m = _OBJ.search(norm)
        obj = (m.group("obj").strip() if m else "")
        if obj in _VAGUE or len(obj.split()) > 6:
            obj = ""
        base = f"Send {who} {obj}" if obj else f"Send {who} what you promised"
        return f"{base} for {route}" if obj and route else base
    return f"Get back to {who} about {route}" if route else f"Get back to {who}"


# ── Due times ────────────────────────────────────────────────────────────────

_WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
             "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50, "couple of": 2, "a couple of": 2}
_NUM = r"(\d{1,3}|a couple of|couple of|an|a|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty-five|forty|fifty)"
_RANGE = rf"{_NUM}(?:\s*(?:-|–|to)\s*{_NUM})?"
_MIN_RE = re.compile(rf"\b(?:in|within|give me|about|around|next)\s+(?:about\s+|around\s+|the\s+next\s+)?{_RANGE}\s*(?:min|mins|minutes)\b")
_HOUR_RE = re.compile(rf"\b(?:in|within|give me|about|around|next)\s+(?:about\s+|around\s+|the\s+next\s+)?{_RANGE}\s*(?:hours?|hrs?)\b")
_HALF_RE = re.compile(r"\bhalf\s+an?\s+(?:hour|hr)\b")
_WITHIN_HOUR_RE = re.compile(r"\bwithin\s+(?:the|an)\s+hour\b|\bin\s+(?:an|one)\s+hour\b")
_CLOCK_RE = re.compile(r"\b(?:by|at|after|around|before|until)\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?![\d:])")
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DAY_RE = re.compile(r"\b(today|tonight|tomorrow|" + "|".join(_DAYS) + r")\b")
_PART_RE = re.compile(r"\b(morning|afternoon|evening)\b")
_VAGUE_TIME_RE = re.compile(r"\b(shortly|soon|asap|as soon as possible|right away|in a bit|in a few minutes|in a little while)\b")


def _num(s: Optional[str]) -> Optional[int]:
    if s is None:
        return None
    s = s.strip()
    return int(s) if s.isdigit() else _WORD_NUM.get(s)


def _top(m: re.Match) -> Optional[int]:
    """The later end of "30-60": a promise is kept by its last minute."""
    vals = [v for v in (_num(m.group(1)), _num(m.group(2))) if v is not None]
    return max(vals) if vals else None


@dataclass(frozen=True)
class Due:
    at: datetime        # UTC
    kind: str           # "said" | "default" | "end_of_day"
    words: str          # what in the text set it ("within the hour"), "" for the default


def _at_local(day, t: time, tz) -> datetime:
    return datetime.combine(day, t, tzinfo=tz).astimezone(timezone.utc)


def default_due(sent_at: datetime, rules: dict) -> Due:
    tz = zone(rules["time_zone"]) or timezone.utc
    local = sent_at.astimezone(tz)
    end = _hm(rules["day_end"])
    rule = rules["default_due"]
    if rule in ("1h", "2h"):
        return Due(sent_at + timedelta(hours=1 if rule == "1h" else 2), "default", "")
    if rule == "end_of_day" and local.time() < end:
        return Due(_at_local(local.date(), end, tz), "default", "")
    return Due(_at_local(local.date() + timedelta(days=1), MORNING, tz), "default", "")


def due_from(text: str, sent_at: datetime, rules: dict, client_zone: Optional[str] = None) -> Due:
    """When the message says it will happen, or the rules' default.

    Relative words first ("within the hour", "in 30-60 mins", "half an hour"),
    then a clock time ("by 5", "at 5:30 pm", read in the client's zone when we
    know it), then a day ("today" = end of the working day, "tomorrow",
    "Monday", with "morning"/"afternoon"/"evening"). "Shortly", "soon" and no
    time at all take the default.
    """
    norm = normalize(text)
    if (m := _WITHIN_HOUR_RE.search(norm)):
        return Due(sent_at + timedelta(hours=1), "said", m.group(0))
    if (m := _MIN_RE.search(norm)) and (n := _top(m)):
        return Due(sent_at + timedelta(minutes=n), "said", m.group(0))
    if (m := _HALF_RE.search(norm)):
        return Due(sent_at + timedelta(minutes=30), "said", m.group(0))
    if (m := _HOUR_RE.search(norm)) and (n := _top(m)):
        return Due(sent_at + timedelta(hours=n), "said", m.group(0))

    biz = zone(rules["time_zone"]) or timezone.utc
    tz = zone(client_zone) or biz
    local = sent_at.astimezone(tz)
    day_m = _DAY_RE.search(norm)
    day_word = day_m.group(1) if day_m else None
    day = local.date()
    if day_word == "tomorrow":
        day = day + timedelta(days=1)
    elif day_word in _DAYS:
        ahead = (_DAYS.index(day_word) - local.weekday()) % 7 or 7
        day = day + timedelta(days=ahead)

    if (c := _CLOCK_RE.search(norm)):
        hour, minute, ampm = int(c.group(1)), int(c.group(2) or 0), (c.group(3) or "").replace(".", "")
        if hour <= 23 and minute <= 59:
            if ampm == "pm" and hour < 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            elif not ampm and 1 <= hour <= 7:
                hour += 12          # "by 5" in a working day is 17:00
            at = datetime.combine(day, time(hour, minute), tzinfo=tz)
            if day_word is None and at <= local:
                at += timedelta(days=1)
            words = (f"{day_word} " if day_word else "") + c.group(0)
            return Due(at.astimezone(timezone.utc), "said", words.strip())

    if day_word in ("today", "tonight"):
        end_local = datetime.combine(sent_at.astimezone(biz).date(), _hm(rules["day_end"]), tzinfo=biz)
        if sent_at < end_local:
            return Due(end_local.astimezone(timezone.utc), "end_of_day", day_word)
        return default_due(sent_at, rules)
    if day_word is not None:
        part = _PART_RE.search(norm)
        t = {"afternoon": time(14, 0), "evening": time(18, 0)}.get(part.group(1) if part else "", MORNING)
        words = day_word + (f" {part.group(1)}" if part else "")
        return Due(_at_local(day, t, tz), "said", words)
    return default_due(sent_at, rules)


# ── Rules → "Try a sentence" ─────────────────────────────────────────────────

KIND_LABEL = {"call": "Call", "email": "E-mail", "todo": "To-do"}


def try_sentence(sentence: str, rules: dict, now: datetime) -> dict:
    """What the rules would make of a sentence an agent writes, for Tasks → Rules."""
    c = catch_promise(sentence, rules["phrases"]) if rules["catch_promises"] else None
    if not c:
        reason = "Catching promises is off." if not rules["catch_promises"] else (
            "A question isn't a promise." if (sentence or "").strip().endswith("?") else "No phrase in the list matches it."
        )
        return {"makes_task": False, "reason": reason}
    d = due_from(c.sentence, now, rules)
    return {
        "makes_task": True, "kind": c.kind, "kind_label": KIND_LABEL[c.kind], "phrase": c.phrase,
        "due_at": d.at.isoformat(), "due_kind": d.kind, "due_words": d.words,
    }
