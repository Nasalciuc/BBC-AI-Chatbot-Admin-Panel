"""The follow-up lottery: a closed lead with a trip and a contact that nobody has is given to an agent
who is at the desk, in turn, and the agent confirms it.

Why (PROBLEME.md #2, 21 Nov 2026): about 7 such leads a day had nobody on them, most from the night,
and someone shared them out by hand the next day (22 chats on 20-21 Nov).

What March-May taught (routing.py, #212):
  - kept: chance from `secrets`, the least loaded first, the same agent for a returning client, one
    database write deciding the winner;
  - avoided: is_ready ("a button left on overnight lies"); present means the panel beat in the last
    90 s (queue_presence_window_seconds), the rule that ended the June phantoms; and the shared line
    of open chats is not touched: the first to Take still wins there (D1). This changes D4 for closed
    leads only, and says so on every row ("given by the lottery to Charlie at 09:12").

A candidate (from the leads universe, the rows the Offers page is made of):
  - the Offers rule (offers_view.candidates): sales, a contact, a route, made in the last 3 days, not
    a CRM test, its offer not marked;
  - the chat is closed or completed (an open chat belongs to the shared line);
  - nobody is on it: no assigned agent, no agent closed it, no agent ever wrote in it;
  - a real destination (not AAA), not a decoy, no open task with someone on it, not given already.
Who may be given one: present, an operator who serves sales, active, chat on.
How, per client (chats that share an e-mail or a phone go to one agent):
  1. the agent who had this client in the last 90 days, if present;
  2. else the present agents in random order, then the fewest lottery leads today first;
  3. at most FOLLOWUP_BURST (3) per agent in any FOLLOWUP_BURST_MINUTES (10), so the night's backlog
     is spread over the morning instead of landing on whoever logs in first; an agent who let this
     lead go is not given it again for MISS_COOLDOWN;
  4. assign_closed_if_unowned: a compare-and-swap, one winner even with two runs.
Confirmation: the agent opens the lead or marks its offer within FOLLOWUP_ACK_MINUTES (15); otherwise
it goes back, the miss is counted against them (audit_log followup_missed), and the next run gives it
to someone else.

State: conversations.assigned_agent_id (who has it) and metadata.followup (how and since when), merged
server-side by patch_conv_presence like the offer marks; every step is an audit_log row. The agent's
badge is a Redis set (followup:new:<agent id>, contracts/redis-keys.md). No migration.
Off until FOLLOWUP_LOTTERY. No names, phones or e-mails in the logs: chats and people by id.
"""

from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.flags import flag
from app.infra.redis_client import get_redis, redis_failed
from app.services.conversation_lock import conversation_lock, try_job_lock

logger = logging.getLogger(__name__)

STICKY_DAYS = 90
MISS_COOLDOWN = timedelta(hours=2)
OWNABLE = ("closed", "completed")
_NOT_A_PLACE = {"", "AAA"}

LOTTERY_HEALTH: dict[str, Any] = {
    "runs": 0, "candidates": 0, "assigned": 0, "sticky": 0, "acked": 0, "missed": 0, "manual": 0,
    "lost_race": 0, "no_one_present": 0, "skipped_unreadable": 0, "skipped_held_elsewhere": 0,
    "patch_failed": 0, "badge_failed": 0, "repaired": 0, "waiting_now": 0, "oldest_waiting_s": 0,
    # A give whose follow-up state was not saved and whose chat could not be given back either: kept in
    # UNWIND_KEY and given back at the start of a later run (unwound), or not even kept (unwind_lost).
    "release_failed": 0, "unwound": 0, "unwind_lost": 0,
}

# "<conversation id> <agent id> <given at>": gives to undo. The chat was assigned but its follow-up state (the
# 15-minute clock) was not saved, and giving it back failed too: nothing else would ever find it, an assigned chat
# being nobody's candidate and a chat without a pending follow-up never taken back. The time names that give, so
# a later run undoes it and nothing newer (CodeRabbit #273). The run holds Redis (try_job_lock skips without it),
# so the set is there when it is needed.
UNWIND_KEY = "lottery:unwind"
_UNWIND_KEEP_S = 7 * 24 * 3600

_rng = secrets.SystemRandom()


def enabled() -> bool:
    return bool(flag("FOLLOWUP_LOTTERY", False))


def ack_minutes() -> int:
    return max(1, int(flag("FOLLOWUP_ACK_MINUTES", 15)))


def _s(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _at(value) -> Optional[datetime]:
    raw = _s(value)
    if not raw:
        return None
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:  # noqa: silent — an unreadable time reads as absent; the row is judged without it
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _et_day(at: datetime) -> str:
    """The business day in New York, where the team counts its day."""
    try:
        from zoneinfo import ZoneInfo

        return at.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:  # noqa: silent — tzdata absent on slim images: a fixed EST offset, as closing.py does
        return (at.astimezone(timezone.utc) - timedelta(hours=5)).date().isoformat()


def followup_of(row: dict) -> dict:
    f = row.get("followup")
    return f if isinstance(f, dict) else {}


# ── who is a candidate ──────────────────────────────────────────


def nobody_on_it(row: dict) -> bool:
    return not (row.get("assigned_agent_id") or row.get("closed_by_agent_id") or row.get("last_agent_message_at"))


def is_candidate(row: dict) -> bool:
    """One universe row, already through offers_view.candidates (route, contact, 3 days, not a test)."""
    from app.services.offers_view import mark_of

    if row.get("conversation_status") not in OWNABLE:
        return False
    if (row.get("tunnel") or "sales") != "sales" or row.get("is_decoy"):
        return False
    if _s(row.get("destination_code")).upper() in _NOT_A_PLACE or _s(row.get("origin_code")).upper() in _NOT_A_PLACE:
        return False
    if mark_of(row) or not nobody_on_it(row):
        return False
    return followup_of(row).get("state") not in ("pending", "acked", "manual")


def serves_sales(user: dict) -> bool:
    from app.services.departure import serves_sales as _serves

    return _serves(user)


def may_be_given(user: dict) -> bool:
    return bool(user.get("is_active", True)) and bool(user.get("chat_enabled", True)) and serves_sales(user)


# ── how ─────────────────────────────────────────────────────────


def client_keys(row: dict) -> list[str]:
    """The keys that make two chats one client, without the chat's own id."""
    from app.services.offers_view import _keys

    return _keys(row)[1:]


def agent_of(row: dict) -> Optional[str]:
    return row.get("assigned_agent_id") or row.get("closed_by_agent_id") or row.get("engaged_agent_id") or None


def sticky_agents(rows: list[dict], now: datetime) -> dict[str, tuple[datetime, str]]:
    """client key -> (when, agent) for the newest chat with an agent in the last STICKY_DAYS."""
    since = now - timedelta(days=STICKY_DAYS)
    out: dict[str, tuple[datetime, str]] = {}
    for row in rows:
        agent = agent_of(row)
        when = _at(row.get("conversation_closed_at")) or _at(row.get("created_at"))
        if not agent or not when or when < since:
            continue
        for key in client_keys(row):
            if key not in out or out[key][0] < when:
                out[key] = (when, agent)
    return out


def given_counts(rows: list[dict], now: datetime, burst_minutes: int) -> tuple[dict[str, int], dict[str, int]]:
    """(lottery leads per agent today in New York, per agent in the last burst_minutes)."""
    today = _et_day(now)
    recent_since = now - timedelta(minutes=burst_minutes)
    day: dict[str, int] = {}
    recent: dict[str, int] = {}
    seen: set = set()
    for row in rows:
        f = followup_of(row)
        cid = row.get("conversation_id")
        if f.get("by") not in ("lottery", "sticky") or not f.get("agent_id") or cid in seen:
            continue
        seen.add(cid)
        at = _at(f.get("assigned_at"))
        if not at:
            continue
        agent = f["agent_id"]
        if _et_day(at) == today:
            day[agent] = day.get(agent, 0) + 1
        if at >= recent_since:
            recent[agent] = recent.get(agent, 0) + 1
    return day, recent


def recent_missers(group: list[dict], now: datetime) -> set[str]:
    out: set[str] = set()
    for row in group:
        for item in followup_of(row).get("misses") or []:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                at = _at(item[1])
                if at and now - at < MISS_COOLDOWN:
                    out.add(str(item[0]))
    return out


def pick(
    eligible: list[str],
    *,
    sticky: Optional[str],
    today: dict[str, int],
    recent: dict[str, int],
    missed: set[str],
    burst: int,
    daily_cap: int,
    rng=None,
) -> tuple[Optional[str], str]:
    """(agent id, "sticky" | "lottery") or (None, why nobody)."""
    rng = rng or _rng

    def free(agent: str) -> bool:
        if agent in missed or recent.get(agent, 0) >= burst:
            return False
        return not daily_cap or today.get(agent, 0) < daily_cap

    if sticky and sticky in eligible and free(sticky):
        return sticky, "sticky"
    pool = [a for a in eligible if free(a)]
    if not pool:
        return None, "no_one_present" if not eligible else "all_busy"
    rng.shuffle(pool)
    pool.sort(key=lambda a: today.get(a, 0))  # stable: among the least loaded, chance decides
    return pool[0], "lottery"


# ── the run ─────────────────────────────────────────────────────


async def present_operators() -> Optional[list[dict]]:
    """The operators at the desk who may be given a lead; None when nobody can say."""
    from app.db import supabase as db
    from app.services.agent_presence import present_operator_ids
    from config.settings import settings

    ids = await present_operator_ids()
    if ids is None:
        rows = await db.get_operator_rows(seen_within_seconds=settings.queue_presence_window_seconds)
    else:
        rows = await db.get_operator_rows(sorted(ids))
    if rows is None:
        return None
    return [u for u in rows if may_be_given(u)]


async def run_lead_lottery() -> dict:
    """One run (every minute, app/services/scheduler.py). Its answer is the job's line in /health."""
    if not enabled():
        return {"state": "off"}
    # Skipped when Redis is configured but can't say who holds the run: two runs in two processes would both
    # give leads out (each give is a swap, but the burst counts would be each run's own). Next minute tries again.
    held, token = await try_job_lock("lead_lottery", ttl_ms=55_000, skip_if_unreachable=True)
    if not held:
        LOTTERY_HEALTH["skipped_held_elsewhere"] += 1
        return {"state": "held_elsewhere"}
    try:
        return await _run_once(datetime.now(timezone.utc))
    finally:
        if token:
            from app.services.conversation_lock import release_job_lock

            await release_job_lock("lead_lottery", token)


async def _run_once(now: datetime) -> dict:
    from app.db import supabase as db
    from app.services.offers_view import candidates as offer_candidates, group_by_client

    LOTTERY_HEALTH["runs"] += 1
    if await _unwind_kept():
        db.invalidate_leads_universe()
    rows = await db.get_leads_universe()
    missed = await _take_back_unconfirmed(rows, now)

    waiting = [r for r in offer_candidates(rows, now=now) if is_candidate(r)]
    LOTTERY_HEALTH["candidates"] = len(waiting)
    if not waiting:
        _waiting_gauge([], now)
        return {"state": "ok", "assigned": 0, "missed": missed, "waiting": 0}

    owners = await db.get_open_task_owners([r.get("conversation_id") for r in waiting])
    agents = await present_operators()
    if owners is None or agents is None:
        LOTTERY_HEALTH["skipped_unreadable"] += 1
        _waiting_gauge(waiting, now)
        return {"state": "unreadable", "assigned": 0, "missed": missed, "waiting": len(waiting)}
    waiting = [r for r in waiting if r.get("conversation_id") not in owners]

    eligible = sorted({u["id"] for u in agents})
    burst = max(1, int(flag("FOLLOWUP_BURST", 3)))
    burst_minutes = max(1, int(flag("FOLLOWUP_BURST_MINUTES", 10)))
    daily_cap = max(0, int(flag("FOLLOWUP_DAILY_CAP", 0)))
    today, recent = given_counts(rows, now, burst_minutes)
    sticky_by_key = sticky_agents(rows, now)

    assigned = sticky_n = 0
    left: list[dict] = []
    groups = group_by_client(waiting)
    groups.sort(key=lambda g: min((_at(r.get("created_at")) or now) for r in g))  # oldest client first
    for group in groups:
        keys = {k for r in group for k in client_keys(r)}
        known = [sticky_by_key[k] for k in keys if k in sticky_by_key]
        sticky = max(known)[1] if known else None
        agent, how = pick(eligible, sticky=sticky, today=today, recent=recent,
                          missed=recent_missers(group, now), burst=burst, daily_cap=daily_cap)
        if not agent:
            if how == "no_one_present":
                LOTTERY_HEALTH["no_one_present"] += 1
            left.extend(group)
            continue
        won = 0
        for row in group:
            if await _give(row, agent, how, now):
                won += 1
                assigned += 1
                sticky_n += how == "sticky"
        # Counted per chat, as given_counts counts them on the next run: one unit both ways. An agent under the
        # burst takes a client whole, however many chats it has.
        today[agent] = today.get(agent, 0) + won
        recent[agent] = recent.get(agent, 0) + won
    if assigned:
        db.invalidate_leads_universe()
    _waiting_gauge(left, now)
    LOTTERY_HEALTH["assigned"] += assigned
    LOTTERY_HEALTH["sticky"] += sticky_n
    return {"state": "ok", "assigned": assigned, "sticky": sticky_n, "missed": missed, "waiting": len(left),
            "present": len(eligible)}


def _waiting_gauge(rows: list[dict], now: datetime) -> None:
    LOTTERY_HEALTH["waiting_now"] = len(rows)
    oldest = min((_at(r.get("created_at")) for r in rows if _at(r.get("created_at"))), default=None)
    LOTTERY_HEALTH["oldest_waiting_s"] = int((now - oldest).total_seconds()) if oldest else 0


def _followup(agent: Optional[str], how: str, now: datetime, *, state: str, misses: list, **extra) -> dict:
    f = {"state": state, "agent_id": agent, "by": how, "assigned_at": now.isoformat(), "misses": misses}
    if state == "pending":
        f["ack_by"] = (now + timedelta(minutes=ack_minutes())).isoformat()
    f.update(extra)
    return f


async def _give(row: dict, agent: str, how: str, now: datetime) -> bool:
    """One chat to one agent: the swap, then how and since when, then the badge."""
    from app.db import supabase as db

    cid = row.get("conversation_id")
    if not cid:
        return False
    async with conversation_lock("followup", cid):
        if not await db.assign_closed_if_unowned(cid, agent):
            LOTTERY_HEALTH["lost_race"] += 1
            return False
        misses = list(followup_of(row).get("misses") or [])
        if not await db.patch_conversation_presence(cid, {"followup": _followup(agent, how, now, state="pending",
                                                                                 misses=misses)}):
            # The swap landed but the follow-up state did not: an assignment without its 15-minute clock would
            # never come back. It is given back at once; the next run tries again.
            LOTTERY_HEALTH["patch_failed"] += 1
            if await db.release_closed_if_agent(cid, agent):
                logger.error(f"[lottery] {cid}: its follow-up state was not saved, the chat given back")
                return False
            # Not given back either: kept, so that a later run gives it back (CodeRabbit #273).
            LOTTERY_HEALTH["release_failed"] += 1
            kept = await _keep_unwind(cid, agent, now)
            logger.error(f"[lottery] {cid}: its follow-up state was not saved and the chat could not be given back; "
                         + ("a later run gives it back" if kept else "NOT kept to give back: check it by hand"))
            return False
    await db.log_audit(None, None, "followup_assigned", "conversations", cid, {"by": how, "agent_id": agent})
    await _badge(agent, add=cid)
    logger.info(f"[lottery] {cid} -> {agent} ({how})")
    return True


async def _keep_unwind(cid: str, agent: str, given_at: datetime) -> bool:
    """Keep a give to undo in UNWIND_KEY. False when Redis would not keep it (counted, the caller logs)."""
    r = get_redis()
    if r is not None:
        try:
            await r.sadd(UNWIND_KEY, f"{cid} {agent} {given_at.isoformat()}")
            await r.expire(UNWIND_KEY, _UNWIND_KEEP_S)
            return True
        except Exception as e:
            redis_failed("lottery unwind", e)
            logger.error(f"[lottery] {cid}: the give to undo was not kept ({type(e).__name__}: {e})")
    LOTTERY_HEALTH["unwind_lost"] += 1
    return False


async def _unwind_kept() -> int:
    """Give back the chats kept in UNWIND_KEY, each only while it is still that agent's and its follow-up is
    still the one the failed give found. A follow-up saved at or after the give is newer than it: the give's own
    clock after all (a lost answer), then maybe confirmed, a give by hand, a later run's give; none of them is
    undone (CodeRabbit #273). A read or a write that fails leaves the entry for the next run. How many were given
    back."""
    from app.db import supabase as db

    r = get_redis()
    if r is None:
        return 0
    try:
        kept = await r.smembers(UNWIND_KEY) or set()
    except Exception as e:
        redis_failed("lottery unwind read", e)
        logger.warning(f"[lottery] the gives to undo were not read ({type(e).__name__}: {e}); next run")
        return 0
    n = 0
    for entry in sorted(kept):
        cid, agent, given_at = (str(entry).split(" ") + ["", ""])[:3]
        given = _at(given_at)
        if not (cid and agent and given):
            logger.error(f"[lottery] a kept give to undo could not be read: {str(entry)[:120]!r}; dropped")
        else:
            async with conversation_lock("followup", cid):
                conv = await db.get_conversation_simple(cid)
                if conv is None:
                    continue  # not read (or gone): kept for the next run
                meta = conv.get("metadata") if isinstance(conv.get("metadata"), dict) else {}
                f = meta.get("followup") if isinstance(meta.get("followup"), dict) else {}
                seen_at = f.get("assigned_at") or None
                seen = _at(seen_at) if seen_at else None
                newer = bool(seen_at) and (seen is None or seen >= given)  # unreadable: not ours to undo
                if conv.get("assigned_agent_id") == agent and not newer:
                    # Only while the follow-up is still the one read here: a write in between keeps the chat.
                    if not await db.release_closed_if_agent(cid, agent, seen_at, no_followup=not seen_at):
                        continue  # still not given back: the next run tries again
                    LOTTERY_HEALTH["unwound"] += 1
                    n += 1
                    logger.warning(f"[lottery] {cid}: the give whose clock was not saved is undone")
        try:
            await r.srem(UNWIND_KEY, entry)
        except Exception as e:
            redis_failed("lottery unwind done", e)
            logger.warning(f"[lottery] {cid}: undone, but still kept ({type(e).__name__}): next run reads it again")
    return n


async def _take_back_unconfirmed(rows: list[dict], now: datetime) -> int:
    """The leads given more than FOLLOWUP_ACK_MINUTES ago and not confirmed go back to the lottery."""
    from app.db import supabase as db

    n = 0
    for row in rows:
        f = followup_of(row)
        due = _at(f.get("ack_by"))
        if f.get("state") != "pending" or not due or due > now:
            continue
        cid = row.get("conversation_id")
        agent = f.get("agent_id")
        if not cid or not agent:
            continue
        async with conversation_lock("followup", cid):
            conv = await db.get_conversation_simple(cid)
            meta = (conv or {}).get("metadata") if isinstance((conv or {}).get("metadata"), dict) else {}
            fresh = meta.get("followup") if isinstance(meta.get("followup"), dict) else {}
            if not conv or fresh.get("state") != "pending" or fresh.get("agent_id") != agent:
                continue  # confirmed, or changed by someone, since the universe was read
            owner = conv.get("assigned_agent_id")
            if owner == agent:
                if not await db.release_closed_if_agent(cid, agent, fresh.get("assigned_at")):
                    continue  # not that assignment any more, or the write failed: the next run sees again
            elif owner:
                continue  # someone else's now: a manager moved it
            else:
                # Released by an earlier run whose miss was not saved: still "pending", nobody's, never a
                # candidate. The miss is written now, and the lead goes back to the lottery.
                LOTTERY_HEALTH["repaired"] += 1
            misses = [*(fresh.get("misses") or []), [agent, now.isoformat()]]
            ok = await db.patch_conversation_presence(cid, {"followup": {**fresh, "state": "missed",
                                                                         "agent_id": None, "misses": misses}})
            if not ok:
                LOTTERY_HEALTH["patch_failed"] += 1
                logger.error(f"[lottery] {cid} taken back from {agent} but the miss was not saved")
        await db.log_audit(None, None, "followup_missed", "conversations", cid, {"agent_id": agent})
        await _badge(agent, remove=cid)
        logger.warning(f"[lottery] {cid}: {agent} did not confirm in {ack_minutes()} min, it goes back")
        n += 1
    if n:
        LOTTERY_HEALTH["missed"] += n
        db.invalidate_leads_universe()
    return n


# ── confirmation, by hand, the badge ────────────────────────────


async def confirm(conversation_id: str, user: dict, followup: Optional[dict], how: str) -> bool:
    """The agent opened the lead (how="opened") or marked its offer (how="marked"): it is theirs for good.

    `followup` is metadata.followup as the caller just read it. Nothing happens unless it is a pending
    follow-up of this very agent. A lead a manager gave them by hand ("manual") is theirs already: opening it
    or marking its offer confirms nothing and only takes it off their badge (contracts/redis-keys.md), which
    nothing else would ever do."""
    from app.db import supabase as db

    uid = user.get("id")
    if isinstance(followup, dict) and followup.get("state") == "manual" and followup.get("agent_id") == uid:
        # Under the lock give_by_hand adds the badge in, so it can't land after this removal (CodeRabbit #273).
        async with conversation_lock("followup", conversation_id):
            await _badge(uid, remove=conversation_id)
        return False
    if not isinstance(followup, dict) or followup.get("state") != "pending" or followup.get("agent_id") != uid:
        return False
    now = datetime.now(timezone.utc)
    ok = False
    async with conversation_lock("followup", conversation_id):
        # What the caller read may be stale (a manager gave the chat to someone else meanwhile): the chat is
        # read again under the lock, and only a still-pending follow-up of this agent, still theirs, confirms.
        conv = await db.get_conversation_simple(conversation_id)
        meta = (conv or {}).get("metadata") if isinstance((conv or {}).get("metadata"), dict) else {}
        fresh = meta.get("followup") if isinstance(meta.get("followup"), dict) else {}
        if (not conv or conv.get("assigned_agent_id") != uid or fresh.get("state") != "pending"
                or fresh.get("agent_id") != uid):
            return False
        ok = await db.patch_conversation_presence(
            conversation_id, {"followup": {**fresh, "state": "acked", "acked_at": now.isoformat(), "acked_how": how}}
        )
        if ok:
            # Off the badge under the lock a hand-give puts it on with: a give after this confirmation keeps its
            # badge, where a removal after the lock (and the audit's wait) took it off again (CodeRabbit #273).
            await _badge(uid, remove=conversation_id)
    if not ok:
        LOTTERY_HEALTH["patch_failed"] += 1
        logger.error(f"[lottery] {conversation_id}: confirmation by {uid} not saved")
        return False
    await db.log_audit(uid, user.get("email"), "followup_acked", "conversations", conversation_id, {"how": how})
    db.invalidate_leads_universe()
    LOTTERY_HEALTH["acked"] += 1
    return True


async def give_by_hand(conversation_id: str, agent: dict, manager: dict) -> dict:
    """A manager gives a closed lead to an agent (the transfer refuses closed chats). No message in the
    chat. Returns the follow-up written; raises ValueError with what the panel says when it can't."""
    from app.db import supabase as db

    conv = await db.get_conversation_simple(conversation_id)
    if not conv:
        raise LookupError("Chat not found")
    if conv.get("status") not in OWNABLE:
        raise ValueError("Only a closed chat is given here; an open one is transferred.")
    if not may_be_given(agent):
        raise ValueError("This person can't take sales chats.")
    now = datetime.now(timezone.utc)
    previous: Optional[str] = None
    f: dict = {}
    async with conversation_lock("followup", conversation_id):
        # Who holds it, and its follow-up, read under the lock: two managers giving the chat at once each move
        # the badge from the owner the other one left, never from one both read before (CodeRabbit #273).
        locked = await db.get_conversation_simple(conversation_id)
        if not locked:
            raise LookupError("Chat not found")
        meta = locked.get("metadata") if isinstance(locked.get("metadata"), dict) else {}
        before = meta.get("followup") if isinstance(meta.get("followup"), dict) else {}
        previous = locked.get("assigned_agent_id")
        f = _followup(agent["id"], "manual", now, state="manual", misses=list(before.get("misses") or []),
                      given_by=manager.get("id"))
        try:
            given = await db.assign_closed_to(conversation_id, agent["id"])
        except Exception as e:
            logger.error(f"[lottery] {conversation_id}: giving by hand failed: {type(e).__name__}: {e}")
            raise RuntimeError("The chat could not be given. Try again.") from e
        if not given:
            # Reopened since it was read: the write is conditional on the status, so it did not land.
            raise ValueError("Only a closed chat is given here; an open one is transferred.")
        if not await db.patch_conversation_presence(conversation_id, {"followup": f}):
            LOTTERY_HEALTH["patch_failed"] += 1
            logger.error(f"[lottery] {conversation_id} given by hand but its follow-up state was not saved")
        # The badges move under the same lock as the state: an open that reads "manual" takes the lead off the
        # badge only after it is on it (confirm), whichever comes first (CodeRabbit #273).
        if previous and previous != agent["id"]:
            await _badge(previous, remove=conversation_id)
        await _badge(agent["id"], add=conversation_id)
    await db.log_audit(manager.get("id"), manager.get("email"), "followup_assigned", "conversations",
                       conversation_id, {"by": "manual", "agent_id": agent["id"], "previous": previous})
    db.invalidate_leads_universe()
    LOTTERY_HEALTH["manual"] += 1
    return f


def _badge_key(agent: str) -> str:
    return f"followup:new:{agent}"


def _badge_floor_key(agent: str) -> str:
    """The score of the last lead given to the agent: it outlives an empty badge (contracts/redis-keys.md)."""
    return f"followup:last:{agent}"


# How long the floor outlives the agent's last give: longer than any panel session keeps the score it saw.
BADGE_FLOOR_TTL_S = 30 * 24 * 3600

# A lead put on an agent's badge, scored strictly after every lead given to that agent before: the time it was
# given, or a millisecond after the last one given (the floor, KEYS[2]) or the badge's newest when this process's
# clock says otherwise (a step back, another process behind it). The panel announces a lead given after the last
# one it saw, and keeps that score while the badge is empty: without the floor, the first lead after an emptied
# badge took this process's clock alone, and one behind kept it quiet (CodeRabbit #273). One script: two gives at
# once can't both read the same floor. %.17g: the stored floor is the score itself, not a rounded one.
BADGE_ADD_LUA = """
local s = tonumber(ARGV[2])
local top = redis.call('ZREVRANGE', KEYS[1], 0, 0, 'WITHSCORES')
if top[2] ~= nil and tonumber(top[2]) >= s then s = tonumber(top[2]) + 0.001 end
local floor = tonumber(redis.call('GET', KEYS[2]) or '0') or 0
if floor >= s then s = floor + 0.001 end
local kept = string.format('%.17g', s)
redis.call('ZADD', KEYS[1], kept, ARGV[1])
redis.call('SET', KEYS[2], kept, 'EX', ARGV[3])
return kept
"""


async def _badge(agent: str, *, add: Optional[str] = None, remove: Optional[str] = None) -> None:
    r = get_redis()
    if r is None or not agent:
        return
    try:
        if add:
            # To the microsecond: two leads given in one second keep their order for newest() (CodeRabbit #273).
            await r.eval(BADGE_ADD_LUA, 2, _badge_key(agent), _badge_floor_key(agent), add, f"{time.time():.6f}",
                         str(BADGE_FLOOR_TTL_S))
        if remove:
            await r.zrem(_badge_key(agent), remove)
    except Exception as e:
        redis_failed("followup badge", e)
        LOTTERY_HEALTH["badge_failed"] += 1
        logger.warning(f"[lottery] badge for {agent} not updated ({type(e).__name__}: {e})")


async def new_count(agent: str) -> Optional[int]:
    """How many leads the lottery gave this agent that they have not opened yet; None when Redis can't say."""
    r = get_redis()
    if r is None or not agent:
        return None
    try:
        return int(await r.zcard(_badge_key(agent)))
    except Exception as e:
        redis_failed("followup badge read", e)
        logger.warning(f"[lottery] badge for {agent} not read ({type(e).__name__}: {e})")
        return None


async def newest(agent: str) -> Optional[tuple[str, float]]:
    """The lead given to this agent last that they have not opened yet, and when (unix seconds): the panel's
    alert opens it in one click (opening it confirms it), and announces a lead only when it was given after the
    last one it saw, whatever the count did meanwhile. None when there is none, or when Redis can't say (logged,
    and counted by redis_failed in /health): the alert then waits for the next beat."""
    r = get_redis()
    if r is None or not agent:
        return None
    try:
        got = await r.zrevrange(_badge_key(agent), 0, 0, withscores=True)
    except Exception as e:
        redis_failed("followup badge read", e)
        logger.warning(f"[lottery] newest lead for {agent} not read ({type(e).__name__}: {e})")
        return None
    if not got:
        return None
    member, score = got[0]
    return str(member), float(score)
