"""The owner's Dashboard — built only from what the server already keeps.

Read-only. Nothing here is new tracking:
  * a chat is "started" when its conversation row is created; the client
    "wrote" when a user message exists; "contact only" means they never did;
  * "given to an agent" is a take in agent_activity_log (claim_won, or the
    rare assigned), "went back to the assistant" is deadline_fired;
  * the first reply is measured from the messages table, from the take to the
    first operator message before the take ended (next take, went back or
    closed). The `first_response` action is logged too rarely to count on;
  * "Ready to quote" is the lead's route, a departure date that wasn't already
    past, travelers and the return (or one way), the client's YES to the
    assistant (conversation metadata confirmed_at) and the CRM accepting the
    lead (leads.created_in_crm). After a take nothing new is read from the
    chat, so a client can't confirm to an agent: those rows stay empty;
  * Ready hours come from agent_ready_log; the AI's cost, answer time and
    template share from pipeline_runs; QA review from leads.reviewed_by_qa;
  * "what the AI had just asked" is a fixed-rule reading of the last assistant
    message — regexes over the text, never a model.

Days are cut at the viewer's local midnight from a fixed UTC offset (the
production image ships without tzdata, see agent_day.py).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from app.services.agent_day import local_day_start, parse_ts

ALLOWED_DAYS = (1, 7, 30)
TAKE_ACTIONS = ("claim_won", "assigned")
REPLY_LINE_S = 60          # Needs attention: a first reply slower than this
REPLY_DEADLINE_S = 90      # the server's deadline (agent_first_response_timeout_seconds)
TAKE_GRACE_S = 120         # a take younger than this can still be answered
WENT_BACK_SHARE = 1 / 3    # Needs attention: a third of an agent's chats
MIN_AGENT_CHATS = 3
MIN_HOUR_WENT_BACK = 3
LATENCY_LINE_MS = 5000     # the AI's typical answer time
REVIEW_WAIT_DAYS = 2       # a ready lead waiting longer than this for QA
LEAVE_SHARE = 0.2          # one question that loses a fifth of the leavers
MAX_IDS_PER_ROW = 60

# ── Reading the assistant's last message (fixed rules) ───────────────────────

# The two sites' own numbers (app/ai/prompts.py SITE_CONFIGS), in any spacing.
_PHONE_888 = re.compile(r"888\)?[\s.\-]*(?:322|668)[\s.\-]*(?:7999|3009)")
_SUMMARY = re.compile(r"is everything correct|reply\s+yes\b|confirm your request", re.I)
_SPECIALIST = re.compile(
    r"connecting you with|check if a specialist is available|consultant on this with you"
    r"|specialist (?:will|is going to) (?:join|be with you)|consultant (?:will|is going to) join",
    re.I,
)
_CALLBACK = re.compile(r"consultant (?:on this )?to call you|consultant will call you|call you (?:on|back)", re.I)
_GOODBYE = re.compile(
    r"takes it from here|in expert hands|will reach out within|will be in touch|will contact you shortly",
    re.I,
)
_ROUTE_BOTH = re.compile(r"from and to|flying from,? and where to|from,? and where are you (?:flying|headed)", re.I)
_FROM = re.compile(
    r"(?:flying|departing|leaving|fly) (?:out )?(?:from|of)\b|where (?:will you be|are you|are you be) (?:flying|departing|starting)"
    r"|which (?:us |u\.s\. )?(?:city|airport) (?:are you|will you be) (?:flying|departing|leaving)|where.*\bfrom\?",
    re.I,
)
_TO = re.compile(r"where (?:are you|will you be) (?:headed|heading|going|flying to)|flying to\?|where to\?|which destination|and where are you headed", re.I)
_FLEX = re.compile(r"fixed (?:on that )?dates?|flexib|wiggle room|day or two", re.I)
_DATES = re.compile(
    r"when (?:are|were|would) you (?:looking|planning|hoping|thinking|like) to|when would you (?:fly|return|like)"
    r"|what date|which dates?|when (?:do|will) you (?:want to )?(?:travel|fly|depart|leave|return)|fly back|return date|rough month",
    re.I,
)
_TRAVELERS = re.compile(r"how many (?:travel|people|passengers|adults|will be|seats|of you)|both adults|are (?:both|all) (?:travelers )?adults", re.I)
_OCCASION = re.compile(r"occasion", re.I)
_CONTACT = re.compile(r"e-?mail|phone number|best number|number to reach", re.I)

LEAVE_LABELS = {
    "from": "The AI had asked “Where are you flying from?”",
    "to": "The AI had asked where they fly to",
    "route": "The AI had asked “Where are you flying from and to?”",
    "dates": "The AI had asked for the dates",
    "flexible": "Asked if the dates are flexible",
    "travelers": "Asked how many travelers",
    "occasion": "Asked about the occasion",
    "contact": "Asked for a phone or an e-mail",
    "summary": "Saw the summary, never said YES",
    "specialist": "Told “a specialist will join”",
    "callback": "Promised a call back",
    "goodbye": "Told “your consultant takes it from here”",
    "phone": "Sent to the phone (+1 888…)",
    "other": "Something else: thanks, a booking they have, a price",
}


def mentions_phone(text: Any) -> bool:
    return isinstance(text, str) and bool(_PHONE_888.search(text))


def _last_question(text: str) -> str:
    """The last sentence that asks something, or the whole text."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    questions = [p for p in parts if p.rstrip().endswith("?")]
    return questions[-1] if questions else text


def classify_last_ai(text: Any) -> str:
    """What the assistant had just said when the client stopped writing.

    Statements that end a chat (the phone, the summary, a promised person, a
    goodbye) are read on the whole message; questions on the last sentence
    that asks something, so "Barcelona it is. Where will you be flying from?"
    is about the origin, not about Barcelona."""
    if not isinstance(text, str) or not text.strip():
        return "other"
    if mentions_phone(text):
        return "phone"
    if _SUMMARY.search(text):
        return "summary"
    if _CALLBACK.search(text):
        return "callback"
    if _SPECIALIST.search(text):
        return "specialist"
    q = _last_question(text)
    asks = q.rstrip().endswith("?")
    if asks:
        if _ROUTE_BOTH.search(q):
            return "route"
        # Dates before places: "what date are you looking to fly from LAX
        # to Manila?" asks for a date, the airports are only context.
        if _FLEX.search(q):
            return "flexible"
        if _DATES.search(q):
            return "dates"
        if _TO.search(q):
            return "to"
        if _FROM.search(q):
            return "from"
        if _TRAVELERS.search(q):
            return "travelers"
        if _OCCASION.search(q):
            return "occasion"
        if _CONTACT.search(q):
            return "contact"
    if _GOODBYE.search(text):
        return "goodbye"
    return "other"


# ── Where a chat came from ───────────────────────────────────────────────────

_SOURCE_NAMES = {
    "fb": "Facebook", "facebook": "Facebook", "ig": "Instagram", "instagram": "Instagram",
    "google": "Google", "bing": "Bing", "kayak": "Kayak", "trustpilot": "Trustpilot",
}
SITE_NAMES = {"bbc": "Buy Business Class", "bct": "Business Class Tickets"}


def source_of(raw: Any) -> str:
    """utm_source in words. "Google" and "google " are one source."""
    if not isinstance(raw, str) or not raw.strip():
        return "No source"
    key = raw.strip().lower()
    return _SOURCE_NAMES.get(key, key[:1].upper() + key[1:])


def device_of(ua: Any) -> str:
    """Same rule as the panel's deviceOf (features/chats/view.ts)."""
    if not isinstance(ua, str) or not ua:
        return "Unknown"
    if re.search(r"iPad|Tablet", ua, re.I):
        return "Tablet"
    if re.search(r"iPhone|Android|Mobile", ua, re.I):
        return "Phone"
    return "Computer"


def page_of(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        return "Unknown"
    try:
        path = urlparse(url.strip()).path or "/"
    except ValueError:  # noqa: silent — an unparseable page URL is grouped as "Unknown" on a read-only chart; nothing is written or sent
        return "Unknown"
    return path.rstrip("/") or "/"


def site_of(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return "Unknown"
    return SITE_NAMES.get(raw.strip(), raw.strip())


# ── Small helpers ────────────────────────────────────────────────────────────

def _med(values: Iterable[Optional[float]]) -> Optional[int]:
    vals = [v for v in values if v is not None]
    return int(round(median(vals))) if vals else None


def _local(ts: datetime, offset_minutes: int) -> datetime:
    return ts + timedelta(minutes=offset_minutes)


def _norm_text(text: Any) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()) if isinstance(text, str) else ""


def _first_name(name: Any) -> str:
    return name.strip().split()[0] if isinstance(name, str) and name.strip() else ""


def _has(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def ready_steps(lead: Optional[dict], conv: dict) -> list[bool]:
    """The five steps to Ready to quote, each one on top of the previous."""
    lead = lead or {}
    created = parse_ts(conv.get("created_at"))
    route = bool(lead.get("origin_code") and lead.get("destination_code"))
    dep = lead.get("departure_date")
    future = False
    if route and isinstance(dep, str) and created:
        try:
            future = datetime.fromisoformat(dep[:10]).date() >= created.date()
        except ValueError:  # noqa: silent — an unreadable departure date simply doesn't count as a date in the future on a read-only funnel
            future = False
    trip = future and bool(lead.get("passengers")) and (
        lead.get("trip_type") == "one_way" or bool(lead.get("return_date"))
    )
    confirmed = trip and bool(conv.get("confirmed_at"))
    ready = confirmed and bool(lead.get("created_in_crm"))
    return [route, future, trip, confirmed, ready]


# ── Ready hours ──────────────────────────────────────────────────────────────

def ready_intervals(rows: list[dict], start: datetime, end: datetime) -> dict[str, list[tuple[datetime, datetime]]]:
    """Ready stretches per user, clipped to [start, end). A stretch starts at a
    "ready" row; a "not ready" with no "ready" before it in the read starts
    nothing. The read begins a day before the earliest period and no stretch
    lasts that long (longest in 60 days: 12.4 h, 8 Nov 2026), so such a row is
    a repeated "not ready", not the end of a stretch the read missed. Counting
    from the start of the read would credit hours nobody was Ready."""
    by_user: dict[str, list[tuple[datetime, bool]]] = defaultdict(list)
    for r in rows:
        at = parse_ts(r.get("changed_at"))
        uid = r.get("user_id")
        if at and uid:
            by_user[uid].append((at, bool(r.get("is_ready"))))
    out: dict[str, list[tuple[datetime, datetime]]] = {}
    for uid, evs in by_user.items():
        evs.sort(key=lambda e: e[0])
        spans: list[tuple[datetime, datetime]] = []
        on_since: Optional[datetime] = None
        for at, ready in evs:
            if ready and on_since is None:
                on_since = at
            elif not ready and on_since is not None:
                spans.append((on_since, at))
                on_since = None
        if on_since is not None:
            spans.append((on_since, end))
        clipped = [(max(a, start), min(b, end)) for a, b in spans if b > start and a < end]
        if clipped:
            out[uid] = clipped
    return out


# ── Takes ────────────────────────────────────────────────────────────────────

def build_takes(activity: list[dict], agent_msgs: list[dict], now: datetime) -> list[dict]:
    """One row per take: who, when, the first reply in it, how it ended.

    A take lasts until the next take on that chat, the deadline sending it
    back to the assistant, or the chat being closed. A second take by the
    same person with nothing in between is the same take (claim_won and
    assigned can both be logged for one claim)."""
    events = []
    for a in activity:
        at = parse_ts(a.get("happened_at"))
        if at and a.get("conversation_id") and a.get("action"):
            events.append({**a, "_at": at})
    events.sort(key=lambda e: e["_at"])
    msgs: dict[str, list[datetime]] = defaultdict(list)
    for m in agent_msgs:
        t = parse_ts(m.get("created_at"))
        if t and m.get("conversation_id"):
            msgs[m["conversation_id"]].append(t)
    for v in msgs.values():
        v.sort()

    by_conv: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_conv[e["conversation_id"]].append(e)

    takes: list[dict] = []
    for cid, evs in by_conv.items():
        conv_takes: list[dict] = []
        current: Optional[dict] = None
        for e in evs:
            action = e["action"]
            if action in TAKE_ACTIONS:
                if current and current["user_id"] == e.get("user_id") and current["end_at"] is None:
                    continue
                if current and current["end_at"] is None:
                    current["end_at"], current["ended_by"] = e["_at"], "taken_again"
                current = {
                    "conversation_id": cid, "user_id": e.get("user_id"), "at": e["_at"],
                    "wait_s": e.get("response_seconds"), "end_at": None, "ended_by": None,
                }
                conv_takes.append(current)
            elif action in ("deadline_fired", "closed") and current and current["end_at"] is None:
                current["end_at"], current["ended_by"] = e["_at"], action
        times = msgs.get(cid, [])
        for t in conv_takes:
            first = next(
                (m for m in times if m >= t["at"] and (t["end_at"] is None or m <= t["end_at"])),
                None,
            )
            t["reply_s"] = int((first - t["at"]).total_seconds()) if first else None
            t["fresh"] = t["end_at"] is None and (now - t["at"]).total_seconds() < TAKE_GRACE_S
        takes.extend(conv_takes)
    takes.sort(key=lambda t: t["at"])
    return takes


# ── The page ─────────────────────────────────────────────────────────────────

def chart_days(days: int) -> int:
    return 7 if days == 1 else days


def _period(now: datetime, offset: int, days: int) -> tuple[datetime, datetime, datetime]:
    """This period, and the one before it with the same length: today so far
    is compared with yesterday up to the same time, not with all of it."""
    start = local_day_start(now, offset) - timedelta(days=days - 1)
    span = timedelta(days=days)
    return start, start - span, now - span


def fetch_since(now: datetime, offset: int, days: int) -> datetime:
    """Oldest instant the page reads: the previous period, or the chart's week."""
    start, prev_start, _ = _period(now, offset, days)
    week = local_day_start(now, offset) - timedelta(days=chart_days(days) - 1)
    return min(prev_start, week)


def build_owner_dashboard(
    *,
    now: datetime,
    offset_minutes: int,
    days: int,
    conversations: list[dict],
    leads: dict[str, dict],
    activity: list[dict],
    user_msgs: list[dict],
    agent_msgs: list[dict],
    ai_msgs: list[dict],
    runs: list[dict],
    ready_log: list[dict],
    users: dict[str, dict],
    last_review_at: Optional[str],
) -> dict:
    """Shape every tab. Pure: all inputs are already fetched.

    `conversations` must cover chats created from the start of the previous
    period, plus any chat taken in it; `leads` is keyed by conversation id."""
    start, prev_start, prev_end = _period(now, offset_minutes, days)
    convs = {c["id"]: c for c in conversations if c.get("id")}

    wrote_at: dict[str, datetime] = {}
    for m in user_msgs:
        t = parse_ts(m.get("created_at"))
        cid = m.get("conversation_id")
        if t and cid and (cid not in wrote_at or t > wrote_at[cid]):
            wrote_at[cid] = t
    ai_by_conv: dict[str, list[dict]] = defaultdict(list)
    for m in ai_msgs:
        t = parse_ts(m.get("created_at"))
        if t and m.get("conversation_id"):
            ai_by_conv[m["conversation_id"]].append({**m, "_at": t})
    for v in ai_by_conv.values():
        v.sort(key=lambda m: m["_at"])

    takes = build_takes(activity, agent_msgs, now)
    first_take: dict[str, datetime] = {}
    for t in takes:
        first_take.setdefault(t["conversation_id"], t["at"])

    def created(c: dict) -> Optional[datetime]:
        return parse_ts(c.get("created_at"))

    def in_window(ts: Optional[datetime], a: datetime, b: datetime) -> bool:
        return ts is not None and a <= ts < b

    def facts(c: dict) -> dict:
        cid = c["id"]
        lead = leads.get(cid)
        steps = ready_steps(lead, c)
        wrote = cid in wrote_at
        given = cid in first_take
        ais = ai_by_conv.get(cid, [])
        last_ai = ais[-1] if ais else None
        left = wrote and not c.get("confirmed_at") and c.get("status") == "closed"
        return {
            "id": cid, "wrote": wrote, "given": given, "steps": steps, "ready": steps[4],
            "contact_only": not wrote, "ai_only": wrote and not given,
            "last_ai": last_ai, "left": left, "lead": lead,
            "has_phone": bool(c.get("has_phone")),
        }

    all_facts = {cid: facts(c) for cid, c in convs.items()}
    cur = [all_facts[cid] for cid, c in convs.items() if in_window(created(c), start, now)]
    prev = [all_facts[cid] for cid, c in convs.items() if in_window(created(c), prev_start, prev_end)]

    def chats_row(cid: str) -> dict:
        c = convs.get(cid, {})
        # team_id lets a team-scoped viewer keep only the chats they can open,
        # visitor_name lets a supervisor see the same masked name as in
        # Conversations; redact() always takes both out before the page.
        return {
            "id": cid, "first_name": _first_name(c.get("visitor_name")), "chat_number": c.get("chat_number"),
            "team_id": c.get("team_id"), "visitor_name": c.get("visitor_name"),
        }

    # ── Overview cards and chats by day ──
    def cohort(rows: list[dict]) -> dict:
        ready = [r for r in rows if r["ready"]]
        ai_only = [r for r in rows if r["ai_only"]]
        return {
            "started": len(rows),
            "wrote": sum(1 for r in rows if r["wrote"]),
            "contact_only": sum(1 for r in rows if r["contact_only"]),
            "ready": len(ready),
            "ready_ai_only": sum(1 for r in ready if not r["given"]),
            "ready_with_agent": sum(1 for r in ready if r["given"]),
            "ai_only": len(ai_only),
            "ai_only_ready": sum(1 for r in ai_only if r["ready"]),
            "with_agent": sum(1 for r in rows if r["wrote"] and r["given"]),
        }

    by_day = []
    today = local_day_start(now, offset_minutes)
    # "Today" still draws the week, so one bar has something to stand next to.
    for n in range(chart_days(days)):
        d0 = today - timedelta(days=n)
        rows = [all_facts[cid] for cid, c in convs.items() if in_window(created(c), d0, d0 + timedelta(days=1))]
        by_day.append({
            "date": _local(d0 + timedelta(hours=12), offset_minutes).date().isoformat(),
            "ready": sum(1 for r in rows if r["ready"]),
            "other": sum(1 for r in rows if r["wrote"] and not r["ready"]),
            "contact_only": sum(1 for r in rows if r["contact_only"]),
        })
    by_day.reverse()

    # ── Funnel ──
    def funnel_counts(rows: list[dict]) -> list[int]:
        return [sum(1 for r in rows if r["steps"][i]) for i in range(5)]

    time_to_ready = []
    for r in cur:
        if r["ready"] and not r["given"]:
            c, t0 = convs[r["id"]], created(convs[r["id"]])
            t1 = parse_ts(c.get("confirmed_at"))
            if t0 and t1 and t1 >= t0:
                time_to_ready.append((t1 - t0).total_seconds())

    # ── Where people leave (chats only with the AI that wrote and never confirmed) ──
    def leavers(rows: list[dict]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for r in rows:
            if r["ai_only"] and r["left"]:
                key = classify_last_ai((r["last_ai"] or {}).get("content"))
                out[key].append(r["id"])
        return out

    leave_now, leave_before = leavers(cur), leavers(prev)
    leave_total = sum(len(v) for v in leave_now.values())
    leave_rows = sorted(
        (
            {
                "key": k, "label": LEAVE_LABELS[k], "chats": len(ids),
                "before": len(leave_before.get(k, [])),
                "conversations": [chats_row(i) for i in ids[:MAX_IDS_PER_ROW]],
            }
            for k, ids in leave_now.items()
        ),
        key=lambda x: (-x["chats"], x["key"]),
    )

    # ── Where they come from ──
    def group_by(fn, keep: int = 12) -> list[dict]:
        acc: dict[str, dict] = {}
        for r in cur:
            name = fn(convs[r["id"]], r)
            row = acc.setdefault(name, {"name": name, "chats": 0, "wrote": 0, "ready": 0})
            row["chats"] += 1
            row["wrote"] += 1 if r["wrote"] else 0
            row["ready"] += 1 if r["ready"] else 0
        rows = sorted(acc.values(), key=lambda x: (-x["chats"], x["name"]))
        if len(rows) <= keep + 1:
            return rows
        rest = {"name": "Other", "chats": 0, "wrote": 0, "ready": 0, "names": len(rows) - keep}
        for x in rows[keep:]:
            for k in ("chats", "wrote", "ready"):
                rest[k] += x[k]
        return rows[:keep] + [rest]

    def route_of(c: dict, r: dict) -> str:
        lead = r["lead"] or {}
        o, d = lead.get("origin_code"), lead.get("destination_code")
        return f"{o} → {d}" if o and d else "No route"

    sources = {
        "source": group_by(lambda c, r: source_of(c.get("utm_source"))),
        "site": group_by(lambda c, r: site_of(c.get("site"))),
        "device": group_by(lambda c, r: device_of(c.get("user_agent"))),
        "page": group_by(lambda c, r: page_of(c.get("page_url"))),
        "route": group_by(route_of),
    }
    source_spellings = sorted({
        c.get("utm_source") for c in convs.values()
        if isinstance(c.get("utm_source"), str) and c["utm_source"] != c["utm_source"].strip()
    })

    # ── Agents ──
    def agent_block(a: datetime, b: datetime) -> dict:
        tk = [t for t in takes if a <= t["at"] < b]
        convs_given = {t["conversation_id"] for t in tk}
        went_back = {t["conversation_id"] for t in tk if t["ended_by"] == "deadline_fired"}
        replies = [t["reply_s"] for t in tk if t["reply_s"] is not None]
        after_yes = set()
        for t in tk:
            yes = parse_ts(convs.get(t["conversation_id"], {}).get("confirmed_at"))
            if yes and yes < t["at"]:
                after_yes.add(t["conversation_id"])
        never = {
            t["conversation_id"] for t in tk
            if t["reply_s"] is None and t["ended_by"] != "deadline_fired" and not t["fresh"]
        }
        return {
            "given": len(convs_given),
            "first_reply_median_s": _med(replies),
            "replies_measured": len(replies),
            "replies_over_deadline": sum(1 for s in replies if s > REPLY_DEADLINE_S),
            "went_back": len(went_back),
            "after_yes": len(after_yes),
            "never_wrote": len(never),
        }

    agents_now, agents_before = agent_block(start, now), agent_block(prev_start, prev_end)
    spans = ready_intervals(ready_log, start, now)
    prev_spans = ready_intervals(ready_log, prev_start, prev_end)

    by_agent = []
    # Everyone who took a chat in the period or was Ready in it.
    agent_ids = {t["user_id"] for t in takes if t["user_id"] and start <= t["at"] < now} | set(spans)
    for uid in agent_ids:
        u = users.get(uid, {})
        mine = [t for t in takes if t["user_id"] == uid and start <= t["at"] < now]
        mine_prev = [t for t in takes if t["user_id"] == uid and prev_start <= t["at"] < prev_end]
        chats = {t["conversation_id"] for t in mine}
        answered = {t["conversation_id"] for t in mine if t["reply_s"] is not None}
        not_in_crm = [
            cid for cid in answered
            if convs.get(cid, {}).get("has_phone") and not (leads.get(cid) or {}).get("created_in_crm")
        ]
        after_yes = set()
        for t in mine:
            yes = parse_ts(convs.get(t["conversation_id"], {}).get("confirmed_at"))
            if yes and yes < t["at"]:
                after_yes.add(t["conversation_id"])
        by_agent.append({
            "user_id": uid,
            "name": u.get("name") or "Former operator",
            "role": u.get("role"),
            "ready_hours": round(sum((b - a).total_seconds() for a, b in spans.get(uid, [])) / 3600, 1),
            "chats": len(chats),
            "first_reply_median_s": _med(t["reply_s"] for t in mine),
            "first_reply_before_s": _med(t["reply_s"] for t in mine_prev),
            "went_back": len({t["conversation_id"] for t in mine if t["ended_by"] == "deadline_fired"}),
            "after_yes": len(after_yes),
            "never_wrote": len({
                t["conversation_id"] for t in mine
                if t["reply_s"] is None and t["ended_by"] != "deadline_fired" and not t["fresh"]
            }),
            "not_in_crm": len(not_in_crm),
            "not_in_crm_conversations": [chats_row(c) for c in not_in_crm[:MAX_IDS_PER_ROW]],
        })
    by_agent.sort(key=lambda r: (-r["chats"], -r["ready_hours"], r["name"]))

    hours = []
    for h in range(24):
        hours.append({"hour": h, "given": 0, "went_back": 0, "ready_people": 0})
    for t in takes:
        if start <= t["at"] < now:
            row = hours[_local(t["at"], offset_minutes).hour]
            row["given"] += 1
            if t["ended_by"] == "deadline_fired":
                row["went_back"] += 1
    ready_by_hour: dict[int, set] = defaultdict(set)
    shift = timedelta(minutes=offset_minutes)
    for uid, ss in spans.items():
        for a, b in ss:
            # Walk the local clock hours the stretch touches.
            t = (a + shift).replace(minute=0, second=0, microsecond=0) - shift
            while t < b:
                ready_by_hour[_local(t, offset_minutes).hour].add(uid)
                t += timedelta(hours=1)
    for h, people in ready_by_hour.items():
        hours[h]["ready_people"] = len(people)

    # ── Quality ──
    ready_now = [r for r in cur if r["ready"]]
    unreviewed = [r for r in ready_now if not (r["lead"] or {}).get("reviewed_by_qa")]
    waiting_long = []
    for r in unreviewed:
        lc = parse_ts((r["lead"] or {}).get("created_at")) or created(convs[r["id"]])
        if lc and (now - lc) > timedelta(days=REVIEW_WAIT_DAYS):
            waiting_long.append(r)
    oldest_wait = None
    if unreviewed:
        oldest = min((parse_ts((r["lead"] or {}).get("created_at")) or now) for r in unreviewed)
        oldest_wait = oldest.isoformat()

    run_rows = [r for r in runs if in_window(parse_ts(r.get("created_at")), start, now)]
    cost = round(sum(float(r.get("cost") or 0) for r in run_rows), 4)
    latency = _med(r.get("latency_ms") for r in run_rows if r.get("latency_ms"))
    templates = sum(1 for r in run_rows if r.get("had_fallback"))

    price_msgs: dict[str, set] = defaultdict(set)
    for r in runs:
        if r.get("intent_detected") == "price_inquiry" and r.get("conversation_id"):
            price_msgs[r["conversation_id"]].add(r.get("message_id"))

    def review_rows() -> list[dict]:
        phone, price, repeat = [], [], []
        phone_left, price_left, repeat_left = [], [], []
        for r in cur:
            ais = ai_by_conv.get(r["id"], [])
            if not ais:
                continue
            last = ais[-1]
            if any(mentions_phone(m.get("content")) for m in ais):
                phone.append(r["id"])
                if r["left"] and mentions_phone(last.get("content")):
                    phone_left.append(r["id"])
            if r["id"] in price_msgs:
                price.append(r["id"])
                if r["left"] and last.get("id") in price_msgs[r["id"]]:
                    price_left.append(r["id"])
            seen = Counter(_norm_text(m.get("content")) for m in ais if _norm_text(m.get("content")))
            twice = {t for t, n in seen.items() if n >= 2}
            if twice:
                repeat.append(r["id"])
                if r["left"] and _norm_text(last.get("content")) in twice:
                    repeat_left.append(r["id"])
        out = [
            {"key": "phone", "chats": len(phone), "left_after": len(phone_left),
             "conversations": [chats_row(i) for i in (phone_left or phone)[:MAX_IDS_PER_ROW]]},
            {"key": "price", "chats": len(price), "left_after": len(price_left),
             "conversations": [chats_row(i) for i in (price_left or price)[:MAX_IDS_PER_ROW]]},
            {"key": "repeat", "chats": len(repeat), "left_after": len(repeat_left),
             "conversations": [chats_row(i) for i in (repeat_left or repeat)[:MAX_IDS_PER_ROW]]},
        ]
        return sorted(out, key=lambda x: (-x["left_after"], -x["chats"]))

    sent_to_phone = [
        r["id"] for r in cur
        if r["ai_only"] and r["left"] and r["last_ai"] and mentions_phone(r["last_ai"].get("content"))
    ]

    def crm_block(rows: list[dict]) -> dict:
        reached = [r for r in rows if (r["lead"] or {}).get("created_in_crm")]
        confirmed = [r for r in reached if convs[r["id"]].get("confirmed_at")]
        agent = [r for r in reached if not convs[r["id"]].get("confirmed_at") and r["given"]]
        defaults = [r for r in reached if not convs[r["id"]].get("confirmed_at") and not r["given"]]
        missing = [r for r in rows if r["lead"] and r["has_phone"] and not r["lead"].get("created_in_crm")]
        refused = [r for r in missing if r["lead"].get("crm_push_gate_reason")]
        closed_by_agent = [
            r for r in missing
            if not r["lead"].get("crm_push_gate_reason") and convs[r["id"]].get("closed_by_agent_id")
            and convs[r["id"]].get("status") in ("closed", "completed")
        ]
        not_yet = [r for r in missing if r not in refused and r not in closed_by_agent]
        return {
            "reached": len(reached),
            "confirmed": len(confirmed),
            "from_agent_chats": len(agent),
            "defaults": len(defaults),
            "not_in_crm": len(missing),
            "closed_by_agent": len(closed_by_agent),
            "refused": len(refused),
            "refused_reasons": dict(Counter(r["lead"].get("crm_push_gate_reason") for r in refused)),
            "not_sent_yet": len(not_yet),
        }

    qa_accounts = sum(1 for u in users.values() if u.get("role") == "qa" and u.get("is_active"))

    # ── Needs attention (fixed lines) ──
    attention: list[dict] = []
    for a in by_agent:
        reply = a["first_reply_median_s"]
        if (
            a["chats"] >= MIN_AGENT_CHATS and reply is not None and reply > REPLY_LINE_S
            and a["went_back"] >= a["chats"] * WENT_BACK_SHARE
        ):
            attention.append({
                "kind": "agent", "level": "urgent", "user_id": a["user_id"], "name": a["name"],
                "went_back": a["went_back"], "chats": a["chats"], "first_reply_s": reply,
                "ready_hours": a["ready_hours"],
            })
    worst = max(hours, key=lambda h: (h["went_back"], h["given"]))
    if worst["went_back"] >= MIN_HOUR_WENT_BACK:
        attention.append({
            "kind": "hour", "level": "check", "hour": worst["hour"], "went_back": worst["went_back"],
            "given": worst["given"], "ready_people": worst["ready_people"],
        })
    if waiting_long:
        attention.append({
            "kind": "review", "level": "check", "waiting": len(unreviewed),
            "waiting_long": len(waiting_long), "last_review_at": last_review_at,
        })
    if latency is not None and latency > LATENCY_LINE_MS:
        attention.append({"kind": "ai_latency", "level": "check", "latency_ms": latency, "line_ms": LATENCY_LINE_MS})

    sites = sorted({site_of(convs[r["id"]].get("site")) for r in cur} - {"Unknown"})

    return {
        "days": days,
        "tz_offset_minutes": offset_minutes,
        "now": now.isoformat(),
        "range_start": start.isoformat(),
        "previous": {"start": prev_start.isoformat(), "end": prev_end.isoformat()},
        "sites": sites,
        "overview": {"now": cohort(cur), "before": cohort(prev), "by_day": by_day},
        "funnel": {
            "ai_only": funnel_counts([r for r in cur if r["ai_only"]]),
            "with_agent": funnel_counts([r for r in cur if r["wrote"] and r["given"]]),
            "time_to_ready_median_s": _med(time_to_ready),
        },
        "leave": {"total": leave_total, "total_before": sum(len(v) for v in leave_before.values()), "rows": leave_rows},
        "sources": sources,
        "source_spellings": [s for s in source_spellings if s],
        "agents": {
            "now": agents_now,
            "before": agents_before,
            "by_agent": by_agent,
            "by_hour": hours,
            "ready_hours_before": {uid: round(sum((b - a).total_seconds() for a, b in ss) / 3600, 1) for uid, ss in prev_spans.items()},
        },
        "quality": {
            "ready": len(ready_now),
            "reviewed": len(ready_now) - len(unreviewed),
            "to_review": len(unreviewed),
            "waiting_long": len(waiting_long),
            "oldest_waiting_since": oldest_wait,
            "last_review_at": last_review_at,
            "qa_accounts": qa_accounts,
            "sent_to_phone": len(sent_to_phone),
            "sent_to_phone_conversations": [chats_row(i) for i in sent_to_phone[:MAX_IDS_PER_ROW]],
            "ai_review": review_rows(),
            "ai": {
                "cost": cost,
                "cost_per_ready": round(cost / len(ready_now), 4) if ready_now else None,
                "answers": len(run_rows),
                "template_answers": templates,
                "latency_median_ms": latency,
                "latency_line_ms": LATENCY_LINE_MS,
            },
            "crm": crm_block(cur),
        },
        "attention": attention,
    }


# ── Scoped views (supervisor, project manager, QA) ───────────────────────────
# Same numbers, cut to what a role may see. The Dashboard of a supervisor or a
# project manager covers their tunnel (the design says "All sales chats"), as
# the old dashboard did; Conversations stays team-scoped, so the chat links on
# the page keep only the chats of their own teams, the ones they can open.

_CHAT_REF_KEYS = frozenset({"id", "first_name", "chat_number", "team_id", "visitor_name"})


def scope_inputs(inputs: dict, tunnel: Optional[str]) -> dict:
    """The same inputs, cut to one tunnel's chats. Ready hours count only the
    people who work that tunnel. `tunnel` None (or "all") keeps everything."""
    if tunnel not in ("sales", "support"):
        return inputs
    convs = [c for c in inputs["conversations"] if c.get("tunnel") == tunnel]
    ids = {c["id"] for c in convs}

    def mine(rows: list[dict]) -> list[dict]:
        return [r for r in rows if r.get("conversation_id") in ids]

    workers = {
        uid for uid, u in inputs["users"].items()
        if u.get("role") == tunnel or u.get("tunnel_scope") in (tunnel, "all")
    }
    return {
        **inputs,
        "conversations": convs,
        "leads": {cid: row for cid, row in inputs["leads"].items() if cid in ids},
        "activity": mine(inputs["activity"]),
        "user_msgs": mine(inputs["user_msgs"]),
        "agent_msgs": mine(inputs["agent_msgs"]),
        "ai_msgs": mine(inputs["ai_msgs"]),
        "runs": mine(inputs["runs"]),
        "ready_log": [r for r in inputs["ready_log"] if r.get("user_id") in workers],
    }


def redact(
    data: dict,
    *,
    mask_names: bool,
    show_cost: bool,
    show_sources: bool,
    team_ids: Optional[set[str]] = None,
) -> dict:
    """What a role may see of a built dashboard. Never mutates `data` (it is
    shared through the cache).

    mask_names: clients' names masked (supervisors), as Conversations masks them.
    show_cost: the AI's cost, answer counts and latency (owner, admin, dev).
    show_sources: marketing sources, sites, devices and pages (owner, admin, dev).
    team_ids: when set, chat links keep only chats of these teams; the counts
    next to them don't change."""
    from app.security.pii import mask_name

    def chat_ref(node: Any) -> bool:
        return isinstance(node, dict) and set(node) == _CHAT_REF_KEYS

    def walk(node: Any) -> Any:
        if chat_ref(node):
            full = node["visitor_name"]
            return {
                "id": node["id"],
                # Masked the way Conversations masks it: the whole name.
                "first_name": (mask_name(full.strip()) or "") if mask_names and isinstance(full, str) and full.strip()
                else "" if mask_names else node["first_name"],
                "chat_number": node["chat_number"],
            }
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [
                walk(x) for x in node
                if not (team_ids is not None and chat_ref(x) and x.get("team_id") not in team_ids)
            ]
        return node

    out = walk(data)
    if not show_cost:
        out["quality"]["ai"] = None
        out["attention"] = [a for a in out["attention"] if a.get("kind") != "ai_latency"]
    if not show_sources:
        out["sources"] = None
        out["source_spellings"] = []
        out["sites"] = []
    return out
