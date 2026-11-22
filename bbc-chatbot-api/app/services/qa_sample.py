"""The QA sample: each morning, the chats QA checks from yesterday. Some at random, and every one that looks
wrong. Nobody can check every chat at this volume, and until now nobody checked any: the last lead QA
reviewed in the panel was from 22 Aug (0 of 494 in the last 30 days, 21 Nov).

At 07:00 New York time (scheduler job qa_sample, QA_SAMPLE on by default), for yesterday's chats that had
an agent and a contact:
  - every chat that looks wrong, with the reason:
      odd_crm_id         a CRM id pasted by hand that does not look like the CRM's own;
      no_crm_after_24h   a day later and still not in the CRM;
      offer_without_crm  the offer marked sent, the lead not in the CRM;
      foreign_contact    the agent wrote a contact that is neither the client's nor ours (the same check
                         that watches Alex, app.ai.output_guard, kinds only);
      closed_fast        the agent closed the chat within a minute of the client's last message;
      low_crm_rate       the agent's CRM rate over 7 days is under half of the team's median;
  - half of the chats of an agent in their first 14 days (new_agent);
  - QA_SAMPLE_PERCENT (15) of the rest, chosen by `secrets` (random).
Each pick is an audit_log row (qa_sample_picked), and so is each verdict (qa_verdict: ok, problem, discuss).
audit_log is read by management only: an agent cannot find out whether a chat of theirs is in the sample.
The QA people (role qa, active) get one e-mail with the chat numbers, the agents and the reasons, never a
client's name or contact. A day is sampled once: its picks and a qa_sample_done row go in one insert (all or
none), and a qa_sample_emailed row marks its e-mail, tried again by the next ticks when it did not go.
"""

from __future__ import annotations

import logging
import math
import secrets
from datetime import datetime, time as dtime, timedelta, timezone
from statistics import median
from typing import Any, Optional

from app.flags import flag

logger = logging.getLogger(__name__)

RUN_AT = dtime(7, 0)
NEW_AGENT_DAYS = 14
NEW_AGENT_SHARE = 0.5
CLOSED_FAST_S = 60
RATE_DAYS = 7
RATE_MIN_LEADS = 5

REASONS = {
    "odd_crm_id": "CRM id that doesn't look like the CRM's",
    "no_crm_after_24h": "Not in the CRM a day later",
    "offer_without_crm": "Offer marked sent, lead not in the CRM",
    "foreign_contact": "The agent wrote a contact that isn't the client's or ours",
    "closed_fast": "Closed within a minute of the client's last message",
    "low_crm_rate": "The agent's CRM rate is under half the team's",
    "new_agent": "The agent's first two weeks",
    "random": "Random",
}
VERDICTS = ("ok", "problem", "discuss")

QA_HEALTH: dict[str, Any] = {
    "runs": 0, "days_sampled": 0, "picked": 0, "suspicious": 0, "emailed": 0, "email_failed": 0,
    "no_recipients": 0, "last_day": None, "verdicts": 0, "save_failed": 0, "operators_unreadable": 0,
    "email_retries": 0,
}
EMAIL_TRIES = 12   # one a scheduler tick (5 min): an hour of retries for the day's e-mail, per process

_done_days: set[str] = set()
_emailed_days: set[str] = set()
_email_tries: dict[str, int] = {}
_rng = secrets.SystemRandom()


def enabled() -> bool:
    return bool(flag("QA_SAMPLE", True))


def _ny():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("America/New_York")
    except Exception:  # noqa: silent — tzdata absent on slim images: a fixed EST offset, as closing.py does
        return timezone(timedelta(hours=-5))


def _at(value) -> Optional[datetime]:
    raw = value.strip() if isinstance(value, str) else ""
    if not raw:
        return None
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:  # noqa: silent — an unreadable time reads as absent; that chat is judged without it
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def day_bounds(day: str) -> tuple[datetime, datetime]:
    """[start, end) of a New York day, in UTC."""
    d = datetime.fromisoformat(day).date()
    start = datetime.combine(d, dtime(0, 0), tzinfo=_ny())
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def day_to_sample(now: datetime) -> Optional[str]:
    """Yesterday in New York once it is RUN_AT there; None before."""
    local = now.astimezone(_ny())
    if local.time() < RUN_AT:
        return None
    return (local.date() - timedelta(days=1)).isoformat()


# ── who is in it (pure) ─────────────────────────────────────────


def _owner(row: dict) -> Optional[str]:
    from app.services.offers_view import owner_of

    return owner_of(row)


def _has_contact(row: dict) -> bool:
    from app.services.offers_view import has_contact

    return has_contact(row)


def population(rows: list[dict], day: str) -> list[dict]:
    """Yesterday's sales chats with an agent and a contact, one row per chat."""
    start, end = day_bounds(day)
    seen: set = set()
    out = []
    for row in rows:
        cid = row.get("conversation_id")
        made = _at(row.get("created_at"))
        if not cid or cid in seen or not made or not (start <= made < end):
            continue
        if (row.get("tunnel") or "sales") != "sales" or row.get("is_decoy"):
            continue
        if not _owner(row) or not _has_contact(row):
            continue
        seen.add(cid)
        out.append(row)
    return out


def low_rate_agents(rows: list[dict], now: datetime) -> set[str]:
    """Agents whose share of leads in the CRM over RATE_DAYS is under half of the team's median."""
    since = now - timedelta(days=RATE_DAYS)
    per: dict[str, list[int]] = {}
    for row in rows:
        made = _at(row.get("created_at"))
        agent = _owner(row)
        if not agent or not made or made < since or not _has_contact(row):
            continue
        tally = per.setdefault(agent, [0, 0])
        tally[0] += 1
        tally[1] += bool(row.get("created_in_crm"))
    rates = {a: crm / n for a, (n, crm) in per.items() if n >= RATE_MIN_LEADS}
    if len(rates) < 2:
        return set()
    team = median(rates.values())
    return {a for a, r in rates.items() if r < team / 2}


def suspicious_reasons(row: dict, now: datetime, *, foreign: bool, low_rate: bool) -> list[str]:
    reasons = []
    crm = row.get("crm") if isinstance(row.get("crm"), dict) else {}
    made = _at(row.get("created_at"))
    if crm.get("shape") == "odd":
        reasons.append("odd_crm_id")
    if not row.get("created_in_crm") and made and now - made >= timedelta(hours=24):
        reasons.append("no_crm_after_24h")
    if row.get("offer_sent_at") and not row.get("created_in_crm"):
        reasons.append("offer_without_crm")
    if foreign:
        reasons.append("foreign_contact")
    closed, last_word = _at(row.get("conversation_closed_at")), _at(row.get("last_user_message_at"))
    if row.get("closed_by_agent_id") and closed and last_word and timedelta(0) <= closed - last_word < \
            timedelta(seconds=CLOSED_FAST_S):
        reasons.append("closed_fast")
    if low_rate:
        reasons.append("low_crm_rate")
    return reasons


def pick(
    chats: list[dict],
    *,
    now: datetime,
    foreign_in: set[str],
    low_rate: set[str],
    new_agents: set[str],
    percent: int,
    rng=None,
) -> list[dict]:
    """[{conversation_id, lead_id, chat_number, agent_id, reasons}] — every suspicious chat, half of a new
    agent's, `percent` of the rest (at least one when there is a rest and percent > 0)."""
    rng = rng or _rng
    picked: list[dict] = []
    rest: list[dict] = []
    for row in chats:
        agent = _owner(row)
        reasons = suspicious_reasons(row, now, foreign=row.get("conversation_id") in foreign_in,
                                     low_rate=agent in low_rate)
        if not reasons and agent in new_agents and rng.random() < NEW_AGENT_SHARE:
            reasons = ["new_agent"]
        if reasons:
            picked.append(_pick_row(row, agent, reasons))
        else:
            rest.append(row)
    if rest and percent > 0:
        n = min(len(rest), max(1, math.ceil(len(rest) * percent / 100)))
        for row in rng.sample(rest, n):
            picked.append(_pick_row(row, _owner(row), ["random"]))
    return picked


def _pick_row(row: dict, agent: Optional[str], reasons: list[str]) -> dict:
    return {"conversation_id": row.get("conversation_id"), "lead_id": row.get("id"),
            "chat_number": row.get("chat_number"), "agent_id": agent, "reasons": reasons}


# ── the run ─────────────────────────────────────────────────────


async def run_qa_sample(now: Optional[datetime] = None) -> dict:
    """The scheduler's job (every 5 min): samples yesterday once it is 07:00 in New York."""
    from app.db import supabase as db

    if not enabled():
        return {"state": "off"}
    now = now or datetime.now(timezone.utc)
    day = day_to_sample(now)
    if not day:
        return {"state": "not_yet"}
    if day in _done_days and day in _emailed_days:
        return {"state": "done", "day": day}
    QA_HEALTH["runs"] += 1
    start, _end = day_bounds(day)
    earlier = await db.get_audit_rows(["qa_sample_done", "qa_sample_empty", "qa_sample_emailed"], start.isoformat())
    marks = {r.get("action") for r in earlier if (r.get("details") or {}).get("day") == day}
    if "qa_sample_emailed" in marks:
        _emailed_days.add(day)
    if marks & {"qa_sample_done", "qa_sample_empty"}:
        _done_days.add(day)
        if day not in _emailed_days:
            return await _email_again(day)
        return {"state": "done", "day": day}
    return await _sample(day, now)


async def _email_again(day: str) -> dict:
    """The day's sample is saved but its e-mail did not go: try again, a few times, from what was saved."""
    tries = _email_tries.get(day, 0)
    if tries >= EMAIL_TRIES:
        return {"state": "done", "day": day, "emailed": False}
    _email_tries[day] = tries + 1
    QA_HEALTH["email_retries"] += 1
    items = (await sample_of(day))["items"]
    picked = [{"chat_number": i["chat_number"], "agent_id": i["agent_id"], "reasons": [r["code"] for r in i["reasons"]]}
              for i in items]
    await _email_and_mark(day, picked)
    return {"state": "done", "day": day, "emailed": day in _emailed_days}


async def _sample(day: str, now: datetime) -> dict:
    from app.ai.output_guard import foreign_contact_kinds
    from app.db import supabase as db

    rows = await db.get_leads_universe()
    chats = population(rows, day)
    start, end = day_bounds(day)
    foreign: set[str] = set()
    if chats:
        # The client's own: the placed phone, the e-mail, and a number kept to check (their words, not a leak).
        contact = {r["conversation_id"]: (r.get("visitor_phone"), r.get("visitor_email"), r.get("phone_to_check"))
                   for r in chats}
        for m in await db.get_agent_messages(list(contact), start.isoformat(), end.isoformat()):
            phone, email, to_check = contact.get(m.get("conversation_id"), (None, None, None))
            if foreign_contact_kinds(m.get("content"), phone, email, also_own=(to_check,)):
                foreign.add(m["conversation_id"])
    agents = await db.get_operator_rows()
    if agents is None:
        # "Nobody" and "could not read" differ (rule 7): without the team, the new agents' half can't be
        # drawn. Nothing is saved; the next tick tries again.
        QA_HEALTH["operators_unreadable"] += 1
        logger.error(f"[qa-sample] {day}: the team could not be read, sampled later")
        return {"state": "unreadable", "day": day}
    new_agents = {a["id"] for a in agents if (_at(a.get("created_at")) or now) > now - timedelta(days=NEW_AGENT_DAYS)}
    picked = pick(chats, now=now, foreign_in=foreign, low_rate=low_rate_agents(rows, now),
                  new_agents=new_agents, percent=max(0, min(100, int(flag("QA_SAMPLE_PERCENT", 15)))))
    # The picks and the day's "done" go in ONE insert: all of them or none, so a day is never marked sampled
    # with half its picks; a failed save leaves the day to the next tick, whole.
    records = [{"action": "qa_sample_picked", "target_table": "conversations", "target_id": p["conversation_id"],
                "details": {"day": day, "reasons": p["reasons"], "agent_id": p["agent_id"], "lead_id": p["lead_id"],
                            "chat_number": p["chat_number"]}} for p in picked]
    records.append({"action": "qa_sample_done", "target_table": "conversations",
                    "details": {"day": day, "chats": len(chats), "picked": len(picked)}})
    try:
        await db.insert_audit_rows_strict(records)
    except Exception as e:
        QA_HEALTH["save_failed"] += 1
        logger.error(f"[qa-sample] {day}: the sample was not saved, sampled again later ({type(e).__name__}: {e})")
        return {"state": "save_failed", "day": day}
    _done_days.add(day)
    suspicious = sum(1 for p in picked if p["reasons"] != ["random"])
    QA_HEALTH.update({"days_sampled": QA_HEALTH["days_sampled"] + 1, "last_day": day})
    QA_HEALTH["picked"] += len(picked)
    QA_HEALTH["suspicious"] += suspicious
    await _email_and_mark(day, picked)
    logger.info(f"[qa-sample] {day}: {len(picked)} of {len(chats)} chats ({suspicious} that look wrong)")
    return {"state": "sampled", "day": day, "chats": len(chats), "picked": len(picked), "suspicious": suspicious}


async def _email_and_mark(day: str, picked: list[dict]) -> None:
    """The day's e-mail, and a qa_sample_emailed row once it is done with (sent, or nothing to send or nobody to
    send it to): a day whose e-mail failed is tried again by the next ticks (_email_again)."""
    from app.db import supabase as db

    if picked and not await _email(day, picked):
        return
    _emailed_days.add(day)
    await db.log_audit(None, None, "qa_sample_emailed", "conversations", None, {"day": day, "picked": len(picked)})


async def _email(day: str, picked: list[dict]) -> bool:
    """True when the e-mail went, or there is nobody with the qa role to send it to; False to try again."""
    from app.db import supabase as db
    from app.services.email import send_qa_sample_email

    try:
        to = await db.get_active_emails_for_role("qa")
        names = await db.get_user_names()
    except Exception as e:
        QA_HEALTH["email_failed"] += 1
        logger.error(f"[qa-sample] {day}: recipients not read, no e-mail yet ({type(e).__name__}: {e})")
        return False
    if not to:
        QA_HEALTH["no_recipients"] += 1
        logger.warning(f"[qa-sample] {day}: nobody has the qa role, no e-mail")
        return True
    lines = [{"chat_number": p["chat_number"], "agent": names.get(p["agent_id"] or "", ""),
              "reasons": [REASONS.get(r, r) for r in p["reasons"]]} for p in picked]
    if await send_qa_sample_email(to, day, lines):
        QA_HEALTH["emailed"] += 1
        return True
    QA_HEALTH["email_failed"] += 1
    return False


# ── the page ────────────────────────────────────────────────────


async def sample_of(day: str) -> dict:
    """The day's sample with the latest verdict on each chat. Raises when audit_log can't be read."""
    from app.db import supabase as db

    start, _ = day_bounds(day)
    rows = await db.get_audit_rows(["qa_sample_picked", "qa_verdict"], start.isoformat())
    names = await db.get_user_names()
    items: dict[str, dict] = {}
    for r in rows:
        d = r.get("details") or {}
        if d.get("day") != day or not r.get("target_id"):
            continue
        cid = r["target_id"]
        if r.get("action") == "qa_sample_picked" and cid not in items:
            items[cid] = {"conversation_id": cid, "chat_number": d.get("chat_number"), "lead_id": d.get("lead_id"),
                          "agent_name": names.get(d.get("agent_id") or "", None), "agent_id": d.get("agent_id"),
                          "reasons": [{"code": c, "label": REASONS.get(c, c)} for c in d.get("reasons") or []],
                          "verdict": None, "note": None, "verdict_by": None, "verdict_at": None}
        elif r.get("action") == "qa_verdict" and cid in items:
            items[cid].update({"verdict": d.get("verdict"), "note": d.get("note"),
                               "verdict_by": names.get(r.get("user_id") or "", None), "verdict_at": r.get("created_at")})
    out = sorted(items.values(), key=lambda i: (i["verdict"] is not None, str(i["chat_number"])))
    counts = {v: sum(1 for i in out if i["verdict"] == v) for v in VERDICTS}
    counts["open"] = sum(1 for i in out if i["verdict"] is None)
    return {"day": day, "items": out, "counts": counts}


async def give_verdict(day: str, conversation_id: str, verdict: str, note: Optional[str], user: dict) -> None:
    """A QA verdict on one chat of the day's sample. ValueError when it is not in that sample."""
    from app.db import supabase as db

    if verdict not in VERDICTS:
        raise ValueError("verdict must be ok, problem or discuss")
    sample = await sample_of(day)
    if not any(i["conversation_id"] == conversation_id for i in sample["items"]):
        raise ValueError("This chat is not in that day's sample")
    # The verdict IS the record: a write that fails raises (the route answers 503), never a silent success.
    await db.insert_audit_rows_strict([{
        "action": "qa_verdict", "target_table": "conversations", "target_id": conversation_id,
        "user_id": user.get("id"), "user_email": user.get("email"),
        "details": {"day": day, "verdict": verdict, "note": (note or "").strip()[:1000] or None},
    }])
    QA_HEALTH["verdicts"] += 1


async def stats(days: int, now: Optional[datetime] = None) -> list[dict]:
    """Per agent over the last `days` sampled days: picked, ok, problem, discuss, still open."""
    from app.db import supabase as db

    now = now or datetime.now(timezone.utc)
    # The last `days` sampled days, by the day each pick names (yesterday in New York and back): a pick is
    # written the morning after its day, so reading by write time alone took in one day too many.
    last = now.astimezone(_ny()).date() - timedelta(days=1)
    wanted = {(last - timedelta(days=i)).isoformat() for i in range(days)}
    first_start, _ = day_bounds(min(wanted))
    rows = await db.get_audit_rows(["qa_sample_picked", "qa_verdict"], first_start.isoformat())
    names = await db.get_user_names()
    agent_of: dict[tuple, str] = {}
    verdict_of: dict[tuple, str] = {}
    for r in rows:
        d = r.get("details") or {}
        if d.get("day") not in wanted:
            continue
        key = (d.get("day"), r.get("target_id"))
        if r.get("action") == "qa_sample_picked" and d.get("agent_id"):
            agent_of[key] = d["agent_id"]
        elif r.get("action") == "qa_verdict":
            verdict_of[key] = d.get("verdict")
    per: dict[str, dict] = {}
    for key, agent in agent_of.items():
        s = per.setdefault(agent, {"agent_id": agent, "agent_name": names.get(agent), "picked": 0, "ok": 0,
                                   "problem": 0, "discuss": 0, "open": 0})
        s["picked"] += 1
        s[verdict_of.get(key) or "open"] += 1
    return sorted(per.values(), key=lambda s: (-s["problem"], -s["picked"]))
